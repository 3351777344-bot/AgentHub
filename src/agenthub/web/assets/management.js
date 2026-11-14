import { segment } from "./api.js";
import { button, card, details, field, form, h, info, link, records, row, select, stack, status, timestamp } from "./ui.js";

const roleLabel = (role) => role === "admin" ? "管理员" : "成员";
const roles = [["member", "成员"], ["admin", "管理员"]];

export async function home(ctx) {
  const links = [
    ["对话", "chat", "创建会话，向模型或知识库提问。"],
    ["知识库", "knowledge", "上传文档、查看入库进度和来源。"],
    ["Agent", "agents", "配置预算和工具，查看步骤并处理审批。"],
    ["工具与 MCP", "tools", "查找工具版本和参数约束。"],
    ["评测", "evaluations", "运行数据集并对比报告。"],
    ["运行日志", "logs", "查看资源记录、错误代码和请求 ID。"],
  ];
  if (ctx.user.role_name === "admin") links.push(["用户管理", "users", "维护账号和角色。"]);
  return stack(
    h("div", { class: "grid" }, links.map(([label, page, description]) => card(label, h("p", {}, description), link("打开" + label, page)))),
    card("账号", h("p", {}, ctx.user.username + " · " + roleLabel(ctx.user.role_name)),
      details("修改密码", form(ctx, "更新密码并退出", [
        field("当前密码", "current_password", { type: "password", autocomplete: "current-password", required: true, maxlength: 128 }),
        field("新密码", "new_password", { type: "password", autocomplete: "new-password", minlength: 12, maxlength: 128, required: true }),
      ], async (data, element) => {
        await ctx.api("/auth/password", { method: "POST", data });
        element.reset();
        document.getElementById("logout").click();
      }))),
  );
}

export async function users(ctx) {
  const accounts = await ctx.list("/users");
  const username = { pattern: "[a-zA-Z0-9_][a-zA-Z0-9_.\\-]{2,63}", required: true, maxlength: 64 };
  const create = form(ctx, "创建用户", [
    field("用户名", "username", username),
    field("初始密码", "password", { type: "password", required: true, minlength: 12, maxlength: 128, autocomplete: "new-password" }),
    select("账号角色", "role", roles),
  ], async (data) => {
    await ctx.api("/users", { method: "POST", data });
    ctx.reload();
  });
  const entries = records(accounts, (account) => [
    row(h("strong", {}, account.username), roleLabel(account.role_name), status(account.is_active ? "enabled" : "disabled")),
    account.id === ctx.user.id ? h("small", {}, "当前账号") : details("编辑 " + account.username, form(ctx, "保存账号状态", [
      select("角色", "role", roles, { value: account.role_name }),
      select("状态", "is_active", [["true", "启用"], ["false", "停用"]], { value: String(account.is_active) }),
    ], async (data) => {
      await ctx.api("/users/" + segment(account.id), { method: "PATCH", data: { ...data, is_active: data.is_active === "true" } });
      ctx.reload();
    })),
  ], "暂无用户。");
  return stack(card("创建账号", create), card("用户列表", entries));
}

export async function logs(ctx) {
  const [filter = ""] = ctx.parts;
  const [calls, audits, runs, batches, bases] = await Promise.all([
    ctx.list("/tool-calls" + (filter ? "?request_id=" + segment(filter) : "")),
    ctx.user.role_name === "admin" ? ctx.list("/audit-events") : [],
    ctx.list("/agent-runs"), ctx.list("/evaluations/runs"), ctx.list("/knowledge-bases"),
  ]);
  return stack(
    card("工具调用", form(ctx, "筛选请求 ID", [
      field("请求 ID", "request_id", { value: filter, maxlength: 64 }),
    ], ({ request_id }) => request_id.trim() ? ctx.go("logs", request_id.trim()) : ctx.go("logs")),
    records(calls, (item) => [
      row(h("strong", {}, (item.tool_name ?? "未识别工具") + (item.tool_version ? " @" + item.tool_version : "")), status(item.status)),
      info([["调用 ID", item.id], ["请求 ID", item.request_id], ["错误码", item.error_code],
        ["耗时", item.duration_ms == null ? "—" : item.duration_ms + " ms"], ["时间", timestamp(item.created_at)]]),
    ], "没有匹配的工具调用。")),
    card("入库任务", records(bases, (base) => link(base.name + " · 查看文档任务", "knowledge", base.id), "暂无知识库。")),
    card("Agent 运行", records(runs, (run) => row(link(run.id, "agents", "run", run.id), status(run.status), run.error_code, timestamp(run.created_at)), "暂无 Agent 运行。")),
    card("评测运行", records(batches, (run) => row(link(run.config.version, "evaluations", "run", run.id), status(run.status), run.error_code, timestamp(run.created_at)), "暂无评测运行。")),
    ctx.user.role_name === "admin" && card("身份审计", records(audits,
      (event) => [h("strong", {}, event.action), info([["时间", timestamp(event.created_at)], ["请求 ID", event.request_id],
        ["操作者 ID", event.actor_id], ["目标账号 ID", event.target_user_id]])], "暂无身份审计。")),
    button("刷新日志", ctx, () => ctx.reload()),
  );
}
