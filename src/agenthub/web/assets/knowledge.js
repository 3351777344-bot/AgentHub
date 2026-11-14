import { segment } from "./api.js";
import { button, card, empty, field, form, h, info, link, number, records, row, split, stack, status, timestamp, watch } from "./ui.js";

const basePath = "/knowledge-bases";
const active = new Set(["queued", "pending", "running", "processing"]);

function deleteButton(ctx, label, path, after) {
  return button(label, ctx, async () => {
    if (!window.confirm("确认" + label + "？已删除的来源将不可用。")) return;
    await ctx.api(path, { method: "DELETE" });
    after();
  }, { class: "danger" });
}

export async function knowledge(ctx) {
  const [baseId, docId, chunkId] = ctx.parts;
  const bases = await ctx.list(basePath);
  const create = form(ctx, "新建知识库", [
    field("知识库名称", "name", { required: true, maxlength: 200 }),
    h("div", { class: "grid" }, number("分块字符数", "chunk_size", 800, 100, 4000), number("重叠字符数", "chunk_overlap", 100, 0)),
  ], async ({ name, chunk_size, chunk_overlap }) => {
    if (Number(chunk_overlap) >= Number(chunk_size)) throw new Error("重叠字符数必须小于分块字符数。");
    const base = await ctx.api(basePath, { method: "POST", data: { name: name.trim(), chunk_size: Number(chunk_size), chunk_overlap: Number(chunk_overlap) } });
    ctx.go("knowledge", base.id);
  });
  const side = stack(card("我的知识库", records(bases, (base) => link(base.name, "knowledge", base.id), "暂无知识库。")), card("新建知识库", create));
  if (!baseId) return split(side, card("文档", empty("选择一个知识库查看文档。")));

  const path = basePath + "/" + segment(baseId);
  const base = await ctx.api(path);
  const upload = form(ctx, "上传并入库", [
    field("文档文件", "file", { type: "file", accept: ".txt,.md,.pdf", required: true }),
  ], async (_, element) => {
    const document = await ctx.api(path + "/documents", { method: "POST", body: new FormData(element) });
    element.reset();
    ctx.go("knowledge", baseId, document.id);
  });
  const docList = h("div");
  let previousDocs = "";
  let listRevision = 0;
  async function refreshList() {
    const revision = ++listRevision;
    const docs = await ctx.list(path + "/documents");
    if (ctx.signal.aborted || revision !== listRevision) return false;
    const snapshot = JSON.stringify(docs);
    if (snapshot !== previousDocs) {
      previousDocs = snapshot;
      docList.replaceChildren(records(docs, (doc) => row(
        link(doc.filename, "knowledge", baseId, doc.id), status(doc.status), doc.error_code,
      ), "暂无文档。上传文件后将在这里显示处理状态。"));
    }
    return docs.some((doc) => active.has(doc.status));
  }
  const right = stack(
    card(base.name, info([["向量模型", base.embedding_model], ["向量维度", base.dimensions], ["分块 / 重叠", base.chunk_size + " / " + base.chunk_overlap]]),
      row(link("在对话中使用", "chat", "base", baseId), deleteButton(ctx, "删除知识库", path, () => ctx.go("knowledge")))),
    card("上传文档", upload),
    card("文档列表", docList, button("刷新文档列表", ctx, refreshList)),
  );
  if (docId) right.append(await documentPanel(ctx, path, baseId, docId, chunkId, refreshList));
  void watch(ctx, refreshList);
  return split(side, right);
}

async function documentPanel(ctx, base, baseId, docId, chunkId, refreshList) {
  const path = base + "/documents/" + segment(docId);
  const doc = await ctx.api(path);
  const holder = h("div");
  let previous = "";
  let revision = 0;
  async function refresh() {
    const current = ++revision;
    try {
      const [latest, jobs] = await Promise.all([ctx.api(path), ctx.list(path + "/jobs")]);
      const chunks = latest.status === "ready" ? await ctx.list(path + "/chunks") : [];
      if (ctx.signal.aborted || current !== revision) return false;
      const snapshot = JSON.stringify([latest, jobs, chunks]);
      if (snapshot !== previous) {
        previous = snapshot;
        const target = chunks.find((chunk) => chunk.id === chunkId);
        holder.replaceChildren(stack(
          row(status(latest.status), latest.error_code, "版本 " + latest.version, latest.chunk_count + " 个片段"),
          latest.status === "failed" && button("重试入库", ctx, async () => {
            await ctx.api(path + "/retry", { method: "POST" });
            void watch(ctx, refreshList);
            void watch(ctx, refresh);
          }),
          h("h3", {}, "处理任务"),
          records(jobs, (job) => [row("版本 " + job.version, status(job.status), job.error_code),
            info([["已处理批次", job.completed_batches], ["Token", job.total_tokens], ["请求 ID", job.request_id],
              ["任务 ID", job.id], ["开始记录", timestamp(job.created_at)], ["结束时间", timestamp(job.finished_at)]])], "暂无任务记录。"),
          h("h3", {}, "索引片段"),
          chunkId && (target
            ? h("article", { class: "note", id: "citation-target" }, h("strong", {}, locationOf(target)), h("p", { class: "prose" }, target.content))
            : empty("该引用的片段已不可用。")),
          records(chunks, (chunk) => h("details", {}, h("summary", {}, locationOf(chunk)), h("p", { class: "prose" }, chunk.content)), "暂无可用片段。"),
        ));
      }
      return active.has(latest.status);
    } catch (error) {
      if (!ctx.signal.aborted && current === revision) {
        previous = "";
        holder.replaceChildren(empty("无法读取文档任务。请刷新状态。"));
        throw error;
      }
      return false;
    }
  }
  holder.append(empty("正在加载文档状态…"));
  void watch(ctx, refresh);
  return card(doc.filename,
    row(button("下载原文", ctx, () => ctx.download(path + "/source", doc.filename)),
      button("刷新文档状态", ctx, () => { void watch(ctx, refresh); }),
      deleteButton(ctx, "删除文档", path, () => ctx.go("knowledge", baseId))),
    holder);
}

export function locationOf(chunk) {
  return "第 " + chunk.page_number + " 页 · 字符 " + chunk.char_start + "–" + chunk.char_end;
}
