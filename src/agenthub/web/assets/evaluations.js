import { segment } from "./api.js";
import { area, button, card, details, empty, field, form, h, info, json, link, localPanel, multi, number, optionRows, optionalNumber, records, row, select, split, stack, status, table, timestamp, watch } from "./ui.js";

const root = "/evaluations";
const active = new Set(["queued", "running"]);
const labelProps = { required: true, pattern: "[a-zA-Z0-9][a-zA-Z0-9_.\\-]{0,63}", maxlength: 64 };
const lines = (value) => value.split(/\r?\n/).map((line) => line.trim()).filter(Boolean);
const datasetName = (item) => item.name + " @" + item.version;
const shortId = (id) => id.slice(0, 8);
const runLabel = (run) => run.config.version + " · " + shortId(run.id);

export async function evaluations(ctx) {
  const [kind, id, candidate] = ctx.parts;
  if (kind === "run") return batchPanel(ctx, id);
  const [datasets, runs] = await Promise.all([ctx.list(root + "/datasets"), ctx.list(root + "/runs")]);
  const side = stack(
    card("评测集版本", link("创建评测集", "evaluations", "new"), records(datasets, (item) => link(datasetName(item), "evaluations", "dataset", item.id), "暂无评测集。")),
    card("运行记录", records(runs, (run) => row(link(runLabel(run), "evaluations", "run", run.id), status(run.status)), "暂无评测运行。")),
  );
  if (kind === "new") return split(side, await datasetForm(ctx));
  if (kind === "dataset") {
    const dataset = await ctx.api(root + "/datasets/" + segment(id));
    return split(side, stack(await datasetPanel(ctx, dataset), compareForm(ctx, runs)));
  }
  return split(side, stack(compareForm(ctx, runs, id, candidate), kind === "compare" && await comparePanel(ctx, id, candidate)));
}

async function datasetForm(ctx) {
  const [bases, tools] = await Promise.all([ctx.list("/knowledge-bases"), ctx.list("/tools?for_agent=true")]);
  const cases = h("div", { class: "stack" });
  const addCase = (kind) => {
    if (cases.children.length >= 30) throw new Error("每个评测集最多 30 个样例。");
    const tags = area("标签（逐行，可选）", "tags");
    const terms = area("预期答案词（逐行）", "answer_contains");
    const fields = h("fieldset", { "data-kind": kind },
      h("legend", {}, kind === "rag" ? "RAG 样例" : "Agent 样例"),
      field("样例键", "key", labelProps),
      area("问题", "question", { required: true, maxlength: 4000 }),
      tags, terms,
    );
    if (kind === "rag") {
      const base = select("样例知识库", "knowledge_base_id", [["", "请选择"], ...optionRows(bases)], { required: true });
      const source = h("div");
      const reload = localPanel(ctx, source, async () => {
        const baseId = base.querySelector("select").value;
        if (!baseId) return empty("先选择样例知识库。");
        const prefix = "/knowledge-bases/" + segment(baseId) + "/documents";
        const docs = await ctx.list(prefix);
        const ready = docs.filter((doc) => doc.status === "ready");
        if (!ready.length) return empty("当前知识库没有已入库文档。");
        const picker = select("金标原文", "document_id", [["", "请选择"], ...optionRows(ready, (doc) => doc.filename)]);
        const chunks = h("div");
        const loadChunks = localPanel(ctx, chunks, async () => {
          const id = picker.querySelector("select").value;
          if (!id) return empty("先选择金标原文。");
          const items = await ctx.list(prefix + "/" + segment(id) + "/chunks");
          return records(items, (chunk) => {
            const known = fields.querySelector("textarea[name='gold_chunk_ids']");
            return [h("p", { class: "prose" }, chunk.content), button("添加片段 " + (chunk.position + 1) + " 为金标", ctx, () => {
              known.value = [...new Set([...lines(known.value), chunk.id])].join("\n");
            })];
          }, "该文档没有可用片段。");
        });
        picker.addEventListener("change", loadChunks);
        return stack(picker, chunks);
      });
      base.addEventListener("change", () => {
        fields.querySelector("textarea[name='gold_chunk_ids']").value = "";
        source.replaceChildren();
        void reload();
      });
      fields.append(base,
        select("预期结论", "outcome", [["answered", "可回答"], ["insufficient_evidence", "证据不足"]]),
        area("金标片段 ID（逐行，可从原文选择）", "gold_chunk_ids"), source);
    } else {
      fields.append(select("预期工具版本", "expected_tools", tools.map((tool) => [tool.name + "@" + tool.version, tool.name + " @" + tool.version]), { multiple: true }),
        area("预期拒绝别名（逐行，可选）", "expected_denials"));
    }
    fields.append(button("移除该样例", ctx, () => fields.remove(), { class: "danger" }));
    cases.append(fields);
    fields.querySelector("input").focus();
  };

  const create = form(ctx, "保存评测集版本", [
    h("div", { class: "grid" }, field("评测集名称", "name", labelProps), field("数据集版本", "version", labelProps)),
    select("声明语料（可包含 Agent 使用的知识库）", "declared", optionRows(bases), { multiple: true }),
    row(button("添加 RAG 样例", ctx, () => addCase("rag")), button("添加 Agent 样例", ctx, () => addCase("agent"))),
    cases, field("已人工复核样例及预期结果", "reviewed", { type: "checkbox", required: true }),
  ], async (data, element) => {
    const declared = new Set(multi(element, "declared"));
    const payload = Array.from(cases.children, (fields) => {
      const value = (name) => fields.querySelector("[name='" + name + "']").value;
      const kind = fields.dataset.kind;
      const common = { kind, key: value("key"), question: value("question").trim(),
        tags: lines(value("tags")), answer_contains: lines(value("answer_contains")) };
      if (kind === "rag") {
        const knowledge_base_id = value("knowledge_base_id");
        declared.add(knowledge_base_id);
        return { ...common, knowledge_base_id, outcome: value("outcome"), gold_chunk_ids: lines(value("gold_chunk_ids")) };
      }
      const expected_tools = Array.from(fields.querySelector("select[name='expected_tools']").selectedOptions, (option) => {
        const [name, version] = option.value.split("@");
        return { name, version: Number(version) };
      });
      return { ...common, expected_tools, expected_denials: lines(value("expected_denials")) };
    });
    if (!payload.length) throw new Error("请添加至少一个样例。");
    const dataset = await ctx.api(root + "/datasets", { method: "POST",
      data: { name: data.name, version: data.version, reviewed: true, knowledge_base_ids: [...declared], cases: payload } });
    ctx.go("evaluations", "dataset", dataset.id);
  });
  return card("新评测集", h("p", {}, "名称、版本和样例键使用字母、数字、下划线、连字符或点。"), create);
}

async function datasetPanel(ctx, dataset) {
  const hasAgent = dataset.data.cases.some((item) => item.kind === "agent");
  const agents = hasAgent ? await ctx.list("/agents") : [];
  const start = form(ctx, "运行评测", [
    field("配置版本", "configuration_version", labelProps),
    hasAgent && select("Agent 配置", "agent_id", [["", "请选择"], ...optionRows(agents, (item) => item.config.name)], { required: true }),
    h("div", { class: "grid" }, number("Top-K（可选）", "top_k", null, 1, 20),
      field("相似度阈值（可选）", "min_similarity", { type: "number", min: 0, max: 1, step: "any" }),
      number("单例超时秒数", "case_timeout_seconds", 90, 1, 900),
      number("批次超时秒数", "run_timeout_seconds", 300, 1, 1800),
      number("生成 Token 上限", "max_completion_tokens", 8192, 1, 65536)),
  ], async (data) => {
    const run = await ctx.api(root + "/runs", { method: "POST", data: {
      dataset_id: dataset.id, configuration_version: data.configuration_version, agent_id: data.agent_id || null,
      top_k: optionalNumber(data, "top_k"), min_similarity: optionalNumber(data, "min_similarity"),
      case_timeout_seconds: Number(data.case_timeout_seconds), run_timeout_seconds: Number(data.run_timeout_seconds),
      max_completion_tokens: Number(data.max_completion_tokens),
    } });
    ctx.go("evaluations", "run", run.id);
  });
  return stack(card(datasetName(dataset),
    info([["样例数", dataset.data.cases.length], ["数据集摘要", dataset.digest], ["语料摘要", dataset.corpus_digest]]),
    details("查看样例和期望值", records(dataset.data.cases, (item) => details(item.key + " · " + item.kind, json(item)), "暂无样例。"))),
  card("发起评测", start));
}

const percent = (value) => value == null ? "—" : (value * 100).toFixed(1) + "%";
const metricRows = [
  ["任务通过率", (r) => percent(r.task_success_rate)],
  ["通过 / 失败 / 跳过", (r) => [r.passed, r.failed, r.skipped].join(" / ")],
  ["Recall@K / 样例数", (r) => percent(r.recall_at_k) + " / " + r.recall_cases],
  ["引用精确率 / 引用数", (r) => percent(r.citation_precision) + " / " + r.citations_total],
  ["工具选择率 / Agent 样例数", (r) => percent(r.tool_selection_rate) + " / " + r.agent_cases],
  ["延迟 P50 / P95（ms）", (r) => (r.latency_p50_ms ?? "—") + " / " + (r.latency_p95_ms ?? "—")],
  ["人工通过 / 已复核", (r) => r.human_passed + " / " + r.human_reviewed],
  ...[["输入", "prompt_tokens"], ["生成", "completion_tokens"], ["向量", "embedding_tokens"]].map(([label, key]) =>
    [label + " Token / 已报告样例", (r) => (r[key].total ?? "—") + " / " + r[key].reported_cases]),
];

async function batchPanel(ctx, id) {
  const path = root + "/runs/" + segment(id);
  const holder = h("div");
  let snapshot = "";
  let revision = 0;
  async function refresh() {
    const current = ++revision;
    const [run, results] = await Promise.all([ctx.api(path), ctx.api(path + "/results")]);
    const report = active.has(run.status) ? null : await ctx.api(path + "/report");
    if (ctx.signal.aborted || revision !== current) return false;
    const next = JSON.stringify([run, results, report]);
    if (snapshot === next) return active.has(run.status);
    snapshot = next;
    const cancel = button("取消评测", ctx, async () => {
      await ctx.api(path + "/cancel", { method: "POST" });
      void watch(ctx, refresh);
    }, { class: "danger" });
    holder.replaceChildren(stack(
      card("运行状态", row(status(run.status), run.error_code, active.has(run.status) && cancel),
        info([["运行 ID", run.id], ["配置版本", run.config.version], ["配置摘要", run.config_digest],
          ["开始", timestamp(run.started_at)], ["结束", timestamp(run.finished_at)]]),
        details("配置快照", json(run.config))),
      card("评测报告", report ? info(metricRows.map(([name, metric]) => [name, metric(report)])) : empty("批次结束后生成报告。")),
      card("逐项结果", records(results, (result) => [
        row(h("strong", {}, result.key), status(result.status), result.failure_stage, result.error_code),
        result.agent_run_id && link("查看 Agent 执行记录", "agents", "run", result.agent_run_id),
        result.observation && details("观察与评分", json({ observation: result.observation, score: result.score })),
        result.review && h("p", {}, "人工复核 · " + (result.review.passed ? "通过" : "未通过") + " · " + result.review.note),
        !active.has(run.status) && result.observation && details("人工复核 " + result.key, form(ctx, "保存复核", [
          select("复核结论", "passed", [["true", "通过"], ["false", "未通过"]], { value: String(result.review?.passed ?? true) }),
          area("复核说明", "note", { required: true, maxlength: 240, value: result.review?.note }),
        ], async ({ passed, note }) => {
          await ctx.api(path + "/results/" + segment(result.key) + "/review", { method: "PUT", data: { passed: passed === "true", note } });
          await refresh();
        })),
      ], "暂无样例执行记录。")),
    ));
    return active.has(run.status);
  }
  if (await refresh()) void watch(ctx, refresh);
  return stack(row(link("返回评测列表", "evaluations"), button("刷新评测状态", ctx, () => { void watch(ctx, refresh); })), holder);
}

function compareForm(ctx, runs, baseline, candidate) {
  const items = runs.filter((run) => !active.has(run.status));
  const options = [["", "请选择"], ...optionRows(items, runLabel)];
  return card("对比报告", items.length < 2 ? empty("至少需要两个已结束的评测运行。") : form(ctx, "比较报告", [
    select("基线运行", "baseline", options, { required: true, value: baseline }),
    select("候选运行", "candidate", options, { required: true, value: candidate }),
  ], (data) => {
    if (data.baseline === data.candidate) throw new Error("请选择两个不同的运行。");
    ctx.go("evaluations", "compare", data.baseline, data.candidate);
  }));
}

async function comparePanel(ctx, baseline_id, candidate_id) {
  const query = new URLSearchParams({ baseline_id, candidate_id });
  try {
    const result = await ctx.api(root + "/compare?" + query);
    return stack(
      card("报告比较",
        table(["指标", "基线 " + runLabel(result.baseline), "候选 " + runLabel(result.candidate)],
          metricRows.map(([name, metric]) => [name, metric(result.baseline_report), metric(result.candidate_report)])),
        info([["退化样例", result.regressions.join("、") || "无"], ["改善样例", result.improvements.join("、") || "无"]])),
      card("样例差异", records(result.cases, (item) => [row(h("strong", {}, item.key), status(item.baseline_status), "→", status(item.candidate_status)),
        info([["基线错误", item.baseline_error], ["候选错误", item.candidate_error], ["召回变化", item.recall_delta],
          ["引用精确率变化", item.citation_precision_delta], ["延迟变化（ms）", item.latency_delta_ms], ["工具选择变化", item.tool_selection_delta]])], "暂无可比较样例。")),
    );
  } catch (error) {
    if (ctx.signal.aborted) throw error;
    return card("无法比较报告", empty(error.message), empty("请选择数据集、语料快照和规则版本一致的两个已结束运行。"));
  }
}
