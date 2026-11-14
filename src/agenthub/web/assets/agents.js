import { ApiError, delay, segment } from "./api.js";
import { events } from "./sse.js";
import { area, button, card, details, empty, explanation, field, form, h, info, json, link, multi, number, optionRows, records, row, select, split, stack, status, timestamp } from "./ui.js";

const active = new Set(["queued", "running"]);
const terminal = new Set(["completed", "failed", "cancelled"]);
const toolKey = (tool) => tool.name + "@" + tool.version;

export async function agents(ctx) {
  const [kind, id] = ctx.parts;
  if (kind === "run") return agentRun(ctx, id);
  const [items, runs] = await Promise.all([ctx.list("/agents"), ctx.list("/agent-runs")]);
  const sidebar = stack(
    card("配置列表", link("新建 Agent", "agents", "new"), records(items, (item) => link(item.config.name, "agents", "config", item.id), "暂无 Agent。")),
    card("运行记录", records(runs, (run) => row(link(items.find((item) => item.id === run.agent_id)?.config.name ?? run.id, "agents", "run", run.id),
      status(run.status), timestamp(run.created_at)), "暂无运行。")),
  );
  const selected = kind === "config" ? await ctx.api("/agents/" + segment(id)) : null;
  if (!selected && kind !== "new") return split(sidebar, card("Agent 配置", empty("创建或选择一个配置。")));
  return split(sidebar, await agentConfig(ctx, selected));
}

async function agentConfig(ctx, selected) {
  const [model, bases, tools, sessions] = await Promise.all([
    ctx.api("/models"), ctx.list("/knowledge-bases"), ctx.list("/tools?for_agent=true"), selected ? ctx.list("/conversations") : [],
  ]);
  const config = selected?.config;
  const selectedTools = config?.tools.map(toolKey) ?? [];
  const baseIds = config?.knowledge_base_ids ?? [];
  const allowedTools = tools.map((tool) => [toolKey(tool), toolKey(tool) + " · " + tool.effect]);
  const allowedBases = optionRows(bases);
  for (const key of selectedTools) if (!allowedTools.some(([id]) => id === key)) allowedTools.push([key, key + " · 当前不可用"]);
  for (const key of baseIds) if (!allowedBases.some(([id]) => id === key)) allowedBases.push([key, key + " · 当前不可用"]);
  const save = form(ctx, "保存 Agent", [
    field("名称", "name", { required: true, maxlength: 120, value: config?.name }),
    area("系统提示词", "system_prompt", { required: true, maxlength: 8000, value: config?.system_prompt }),
    field("模型名称", "model_name", { required: true, readonly: true, value: model.model ?? config?.model_name, placeholder: "由部署管理员配置" }),
    h("fieldset", {}, h("legend", {}, "可选资源"), h("p", {}, "每类最多选择 8 项。按住 Ctrl / ⌘ 可多选。"),
      select("知识库", "bases", allowedBases, { multiple: true, value: baseIds }),
      select("工具版本", "tools", allowedTools, { multiple: true, value: selectedTools })),
    h("div", { class: "grid" },
      number("最大步骤数", "max_steps", config?.max_steps ?? 12, 1, 40),
      number("最长运行秒数", "max_seconds", config?.max_seconds ?? 180, 1, 900),
      number("最大生成 Token", "max_completion_tokens", config?.max_completion_tokens ?? 2048, 1, 32768)),
  ], async (data, element) => {
    if (!model.configured) throw new Error("请先配置部署模型。");
    const knowledge_base_ids = multi(element, "bases");
    const refs = multi(element, "tools");
    if (knowledge_base_ids.length > 8 || refs.length > 8) throw new Error("知识库和工具各最多选择 8 项。");
    const payload = {
      name: data.name.trim(), system_prompt: data.system_prompt.trim(), model_name: data.model_name,
      knowledge_base_ids, tools: refs.map((ref) => {
        const [name, version] = ref.split("@");
        return { name, version: Number(version) };
      }),
      max_steps: Number(data.max_steps), max_seconds: Number(data.max_seconds),
      max_completion_tokens: Number(data.max_completion_tokens),
    };
    const result = await ctx.api(selected ? "/agents/" + segment(selected.id) : "/agents",
      { method: selected ? "PUT" : "POST", data: payload });
    if (selected) ctx.notice("Agent 配置已保存。");
    else ctx.go("agents", "config", result.id);
  });
  return stack(card(config?.name ?? "创建 Agent", save), selected && card("运行 Agent",
    form(ctx, "发起运行", [
      area("运行指令", "prompt", { required: true, maxlength: 8000 }),
      select("保存答案的会话", "conversation_id", [["", "独立运行"], ...optionRows(sessions, (item) => item.title)]),
    ], async ({ prompt, conversation_id }) => {
      const run = await ctx.api("/agents/" + segment(selected.id) + "/runs", {
        method: "POST", data: { prompt: prompt.trim(), conversation_id: conversation_id || null },
      });
      ctx.go("agents", "run", run.id);
    })));
}

async function agentRun(ctx, id) {
  const path = "/agent-runs/" + segment(id);
  const runPanel = h("div");
  const eventList = h("ol", { class: "list" });
  const connection = h("p", { role: "status" });
  const firstEvent = empty("暂无事件。");
  let cursor = 0;
  let snapshot = "";
  let controller;
  let run;
  let refreshRevision = 0;

  async function refresh(signal = ctx.signal) {
    const current = ++refreshRevision;
    const [latest, steps] = await Promise.all([
      ctx.api(path, { signal }), ctx.api(path + "/steps", { signal }),
    ]);
    const approval = latest.approval_id && latest.status === "waiting_approval"
      ? await ctx.api(path + "/approvals/" + segment(latest.approval_id), { signal }) : null;
    if (signal.aborted || current !== refreshRevision) return;
    run = latest;
    const contents = JSON.stringify([latest, steps, approval]);
    if (contents === snapshot) return;
    snapshot = contents;
    runPanel.replaceChildren(renderRun(latest, steps, approval));
  }

  function renderRun(latest, steps, approval) {
    const act = (label, action, props) => button(label, ctx, async () => {
      try {
        await ctx.api(path + "/actions/" + action, { method: "POST" });
      } finally {
        restart();
      }
    }, props);
    const decision = (label, value) => button(label, ctx, async (control) => {
      if (!confirm(label + "这个工具调用？")) return;
      const controls = control.parentElement.querySelectorAll("button");
      for (const item of controls) item.disabled = true;
      try {
        await ctx.api(path + "/approvals/" + segment(approval.id), {
          method: "POST", data: { digest: approval.digest, decision: value },
        });
      } finally {
        for (const item of controls) item.disabled = false;
        restart();
      }
    }, { class: value === "reject" ? "danger" : "primary" });
    return stack(
      card("运行状态", row(status(latest.status), latest.pause_requested && "已请求暂停", latest.error_code),
        info([["运行 ID", latest.id], ["工作流版本", latest.workflow_version], ["更新", timestamp(latest.updated_at)],
          ["输入 / 生成 Token", latest.prompt_tokens + " / " + latest.completion_tokens]]),
        details("运行指令", h("p", { class: "prose" }, latest.prompt)),
        latest.answer && h("div", { class: "prose" }, latest.answer),
        latest.conversation_id && link("查看关联会话", "chat", "session", latest.conversation_id),
        latest.recoverable && explanation("后台允许从已保存的进度恢复。先检查错误码和步骤，再选择恢复。"),
        row(active.has(latest.status) && !latest.pause_requested && act("暂停运行", "pause"),
          (latest.status === "paused" || latest.recoverable) && act("恢复运行", "resume"),
          (!terminal.has(latest.status) || latest.recoverable) && act("取消运行", "cancel", { class: "danger" }))),
      approval && card("待审批工具调用", info([["工具", toolKey(approval.tool)], ["副作用", approval.effect],
        ["调用摘要", approval.digest], ["步骤 ID", approval.step_id]]), h("h3", {}, "实际参数"), json(approval.arguments),
        row(decision("批准", "approve"), decision("拒绝", "reject"))),
      card("执行步骤", records(steps, (step) => [
        row(h("strong", {}, "#" + step.ordinal + " · " + step.kind), status(step.status), step.error_code),
        info([["开始", timestamp(step.created_at)], ["结束", timestamp(step.finished_at)], ["工具调用 ID", step.tool_call_id]]),
      ], "暂无执行步骤。")),
    );
  }

  async function follow(signal) {
    for (;;) {
      try {
        await refresh(signal);
        if (signal.aborted) return;
        connection.textContent = cursor ? "正在从事件 #" + cursor + " 续读…" : "正在读取事件…";
        let refreshAfter = 0;
        for await (const frame of events(path + "/events", { headers: { "Last-Event-ID": String(cursor) }, signal })) {
          if (frame.event === "error") {
            if (frame.data.code === "invalid_session") await ctx.api("/auth/me", { signal });
            throw new ApiError(0, { ...frame.data, request_id: frame.requestId });
          }
          const sequence = Number(frame.id);
          if (!Number.isSafeInteger(sequence) || sequence <= cursor) continue;
          cursor = sequence;
          firstEvent.hidden = true;
          connection.textContent = "已读取事件 #" + cursor;
          eventList.append(h("li", {}, details("#" + cursor + " · " + frame.event + " · " + timestamp(frame.data.created_at), json(frame.data.data))));
          if (Date.now() >= refreshAfter) {
            await refresh(signal);
            refreshAfter = Date.now() + 500;
          }
        }
        await refresh(signal);
        if (signal.aborted) return;
        if (run && !active.has(run.status) && cursor >= run.event_seq) {
          connection.textContent = cursor ? "事件已同步至 #" + cursor : "当前没有运行事件。";
          return;
        }
        connection.textContent = "等待后续事件，将从 #" + cursor + " 续读。";
      } catch (error) {
        if (signal.aborted) return;
        connection.textContent = error.message + " 将从 #" + cursor + " 重连。";
        if (error.status && error.status < 500) {
          connection.textContent = error.message + " 请重新同步运行。";
          return;
        }
      }
      try {
        await delay(2500, signal);
      } catch (error) {
        if (signal.aborted) return;
        throw error;
      }
    }
  }

  function restart() {
    controller?.abort();
    controller = new AbortController();
    void follow(AbortSignal.any([ctx.signal, controller.signal]));
  }

  await refresh();
  restart();
  return stack(link("返回 Agent 列表", "agents"), runPanel, card("运行事件", connection,
    button("重新同步运行", ctx, restart), firstEvent, eventList));
}
