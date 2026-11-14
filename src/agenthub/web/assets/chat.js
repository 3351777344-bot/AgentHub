import { ApiError, delay, segment } from "./api.js";
import { events } from "./sse.js";
import { button, card, empty, explanation, field, form, h, link, localPanel, optionRows, records, row, select, split, stack, status, area } from "./ui.js";
import { locationOf } from "./knowledge.js";

export async function chat(ctx) {
  const [kind, id, baseId] = ctx.parts;
  const activeId = kind === "session" ? id : null;
  const preselectedBase = kind === "base" ? id : baseId;
  const [sessions, bases, model] = await Promise.all([
    ctx.list("/conversations"), ctx.list("/knowledge-bases"), ctx.api("/models"),
  ]);
  const sidebar = stack(
    card("新会话", form(ctx, "创建会话", [
      field("会话标题", "title", { maxlength: 200, placeholder: "新会话" }),
    ], async ({ title }) => {
      const session = await ctx.api("/conversations", { method: "POST", data: { title: title.trim() || "新会话" } });
      ctx.go("chat", "session", session.id, ...baseParts(preselectedBase));
    })),
    card("会话列表", records(sessions, (session) => link(session.title, "chat", "session", session.id, ...baseParts(preselectedBase)), "暂无会话。")),
  );
  const chatArea = activeId
    ? await conversation(ctx, activeId, sessions.find((item) => item.id === activeId), bases, preselectedBase, model)
    : card("开始对话", empty("请创建或选择一个会话。"));
  return split(sidebar, chatArea);
}

function baseParts(base) {
  return base ? [base] : [];
}

async function conversation(ctx, id, session, bases, preselectedBase, model) {
  const path = "/conversations/" + segment(id);
  const transcript = h("div", { class: "transcript stack", tabindex: "0", "aria-label": "会话历史" });
  const live = h("div", { class: "message", hidden: true }, h("strong", {}, "助手"), h("p", { class: "prose" }));
  const feedback = h("p", { role: "status" });
  const refreshButton = button("读取会话历史", ctx, async () => { await reconcile(); });
  let streamController;
  let needsReconcile = false;
  const ragHistory = new Map();
  const stop = button("停止接收", ctx, () => streamController?.abort(), { hidden: true });

  async function fetchHistory() {
    const history = await ctx.list(path + "/messages");
    if (ctx.signal.aborted) return history;
    const messages = history.length ? history.map((message) => messageCard(ctx, path, message, ragHistory)) : [empty("暂无消息。")];
    transcript.replaceChildren(...messages);
    transcript.scrollTop = transcript.scrollHeight;
    return history;
  }

  async function reconcile() {
    const history = await fetchHistory();
    const isPending = history.some((message) => message.status === "pending");
    feedback.textContent = isPending ? "后台仍显示待处理。请稍后读取会话历史确认结果。" : "已读取后台会话状态。";
    refreshButton.hidden = false;
    return !isPending;
  }

  const composer = form(ctx, "发送", [
    select("对话模式", "knowledge_base_id", [["", "普通聊天"], ...optionRows(bases)], {
      value: bases.some((base) => base.id === preselectedBase) ? preselectedBase : "",
    }),
    area("消息", "content", { required: true, maxlength: 16000, rows: 3, placeholder: "输入你的问题" }),
  ], async ({ content, knowledge_base_id }, element) => {
    const prompt = content.trim();
    if (!prompt) throw new Error("请输入消息。");
    if (needsReconcile && !await reconcile()) throw new Error("请等待后台结束上一轮，再发送。");
    const isRag = Boolean(knowledge_base_id);
    const draft = element.elements.content;
    const mode = element.elements.knowledge_base_id;
    mode.disabled = true;
    draft.readOnly = true;
    let done = false;
    let interruption;
    let messageId;
    streamController = new AbortController();
    const signal = AbortSignal.any([ctx.signal, streamController.signal]);
    stop.hidden = false;
    refreshButton.hidden = true;
    live.hidden = false;
    live.querySelector("p").textContent = "";
    feedback.textContent = "正在等待响应…";
    try {
      for await (const frame of events(path + (isRag ? "/rag?stream=true" : "/messages?stream=true"), {
        method: "POST", signal, data: isRag ? { question: prompt, knowledge_base_id } : { content: prompt },
      })) {
        if (frame.event === "start") {
          messageId = frame.data.message_id;
          feedback.textContent = "正在接收回答…";
        } else if (frame.event === "delta") {
          live.querySelector("p").append(frame.data.content);
        } else if (frame.event === "error") {
          throw new ApiError(0, { ...frame.data, request_id: frame.data.request_id ?? frame.requestId });
        } else if (frame.event === "done") {
          if (isRag) ragHistory.set(frame.data.message.id, frame.data);
          done = true;
        }
      }
      if (!done) throw new ApiError(0, { code: "stream_incomplete" });
    } catch (error) {
      if (ctx.signal.aborted) return;
      interruption = streamController.signal.aborted ? "已停止接收。正在核对后台历史。" : error.message;
    } finally {
      stop.hidden = true;
      mode.disabled = false;
      draft.readOnly = false;
    }

    needsReconcile = !done;
    if (done) draft.value = "";
    try {
      if (!done) feedback.textContent = interruption;
      let history = await fetchHistory();
      // The service may still be writing cancellation or completion as the socket closes.
      for (let attempt = 0; !done && attempt < 4 &&
        (messageId ? history.find((item) => item.id === messageId)?.status === "pending" :
          history.some((item) => item.status === "pending")); attempt += 1) {
        await delay(650, ctx.signal);
        history = await fetchHistory();
      }
      if (done || (messageId && history.some((item) => item.id === messageId && item.status === "completed"))) {
        draft.value = "";
        needsReconcile = false;
        feedback.textContent = "回答已写入会话历史。";
      } else {
        feedback.textContent = "已保留输入。检查后台记录后，可按需再次发送。";
        ctx.notice(interruption, true);
      }
      live.hidden = true;
    } catch (error) {
      feedback.textContent = "无法核对后台历史。恢复连接后请先读取历史。";
      ctx.notice(error.message, true);
    } finally {
      refreshButton.hidden = false;
    }
  }, { extra: [stop] });

  await fetchHistory();
  return card(session?.title ?? "会话",
    model.configured ? h("p", {}, "模型 " + model.model) : explanation("模型尚未配置，已有会话仍可查看。"),
    transcript, live, composer, feedback, refreshButton);
}

function messageCard(ctx, path, message, ragHistory) {
  const isAssistant = message.role === "assistant";
  const citations = h("div");
  const cached = ragHistory.get(message.id);
  if (cached) citations.append(renderCitations(cached));
  const reload = localPanel(ctx, citations, async () => {
    try {
      const result = await ctx.api(path + "/messages/" + segment(message.id) + "/rag");
      return renderCitations(result);
    } catch (error) {
      if (error.code === "rag_message_not_found") return empty("该消息来自普通聊天或 Agent。");
      throw error;
    }
  });
  return h("article", { class: "message " + (isAssistant ? "assistant" : "user") },
    row(h("strong", {}, isAssistant ? "助手" : "你"), status(message.status), message.error_code),
    h("div", { class: "prose" }, message.content),
    isAssistant && message.status === "completed" && stack(button("查看引用", ctx, reload), citations));
}

function renderCitations(result) {
  return stack(
    h("p", {}, result.outcome === "insufficient_evidence" ? "结论 · 证据不足" : "结论 · 已回答"),
    records(result.citations, ({ label, source, available }) => [
      h("strong", {}, label + " · " + source.filename),
      h("p", {}, locationOf(source)),
      available ? link("定位引用片段", "knowledge", result.knowledge_base_id, source.document_id, source.chunk_id) : empty("来源已不可用。"),
    ], "该回答没有引用。"),
  );
}
