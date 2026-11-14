// Run only against the isolated Compose project described in docs/TESTING.md.
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";
import { randomUUID } from "node:crypto";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import { resolve } from "node:path";

const base = "http://127.0.0.1:18090";
const adminName = "p9_admin";
const temp = process.env.TEMP ?? process.env.TMPDIR ?? "/tmp";
const adminPassword = process.env.AGENTHUB_P9_ADMIN_PASSWORD ||
  (await readFile(resolve(temp, "agenthub-p9-admin.secret"), "utf8")).trim();
const { chromium, expect } = createRequire(import.meta.url)(
  process.env.AGENTHUB_P9_PLAYWRIGHT ?? resolve(temp, "agenthub-p9-browser", "node_modules", "@playwright", "test")
);
const browser = await chromium.launch({ channel: process.env.AGENTHUB_P9_BROWSER ?? "msedge" });
const memberName = "p9_" + randomUUID().slice(0, 8);
const memberPassword = randomUUID();
const label = randomUUID().slice(0, 8);
const stopped = new Set();
const failures = [];
const byName = (page, name) => page.getByRole("heading", { name, exact: true });
const control = (page, name) => page.getByRole("button", { name, exact: true });
const field = (page, label) => page.getByLabel(label, { exact: true }).filter({ visible: true });
const nav = (page, name) => page.getByRole("navigation").getByRole("link", { name, exact: true });
const click = (page, name) => control(page, name).click();
const fill = (page, name, value) => field(page, name).fill(value);
const seen = (page, text, timeout = 20000) => expect(page.getByText(text, { exact: true }).first()).toBeVisible({ timeout });
const pass = (name) => process.stdout.write("PASS " + name + "\n");

function service(action, name) {
  const container = "agenthub-p9-acceptance-" + name + "-1";
  const command = action === "stop" && name === "api" ? [action, "-t", "1"] : [action];
  execFileSync("docker", [...command, container], { stdio: "ignore" });
  if (action === "stop") stopped.add(name);
  else stopped.delete(name);
}

async function read(token, path, properties = {}) {
  const response = await fetch(base + "/api/v1" + path, {
    ...properties, headers: { Authorization: "Bearer " + token, ...properties.headers },
  });
  return response;
}

async function login(page, username, password) {
  await page.goto(base);
  await fill(page, "用户名", username);
  await fill(page, "密码", password);
  const loggedIn = page.waitForResponse((res) => res.url().endsWith("/auth/login") && res.status() === 200);
  await field(page, "密码").press("Enter");
  const { access_token } = await (await loggedIn).json();
  await expect(byName(page, "工作台")).toBeVisible();
  return access_token;
}

function trap(page) {
  page.on("pageerror", (error) => failures.push(error.message));
  page.on("console", (msg) => {
    if (msg.type() === "error" && /Content Security Policy/.test(msg.text())) failures.push(msg.text());
  });
  page.on("dialog", (dialog) => dialog.accept());
}

async function register(page, prefix, effect, role) {
  await click(page, "发现 local 的工具");
  await page.getByText("注册此工具", { exact: true }).click();
  await fill(page, "平台工具名称", prefix);
  await fill(page, "已复核的说明", "Multiply two bounded integers for the public P9 sample.");
  await field(page, "所需角色").selectOption(role);
  await field(page, "副作用").selectOption(effect);
  await field(page, "我已复核输入输出约束、角色和副作用").check();
  await click(page, "注册工具");
  const record = page.locator("article.record").filter({ hasText: prefix + " @1" }).last();
  await expect(record).toBeVisible();
  return record;
}

try {
  const adminContext = await browser.newContext();
  const memberContext = await browser.newContext({ acceptDownloads: true });
  const admin = await adminContext.newPage();
  const member = await memberContext.newPage();
  trap(admin);
  trap(member);

  await admin.goto(base);
  await admin.keyboard.press("Tab");
  await expect(admin.getByRole("link", { name: "跳到主内容" })).toBeFocused();
  await admin.keyboard.press("Enter");
  await expect(admin.locator("main")).toBeFocused();
  await fill(admin, "用户名", adminName);
  await fill(admin, "密码", randomUUID());
  await field(admin, "密码").press("Enter");
  await expect(admin.getByRole("alert")).toContainText("401");
  const adminToken = await login(admin, adminName, adminPassword);
  const storage = await admin.evaluate(() => [localStorage.length, sessionStorage.length, document.cookie]);
  assert.deepEqual(storage, [0, 0, ""]);
  await nav(admin, "用户管理").press("Enter");
  await expect(byName(admin, "用户管理")).toBeFocused();
  await fill(admin, "用户名", memberName);
  await fill(admin, "初始密码", memberPassword);
  await click(admin, "创建用户");
  await seen(admin, memberName);
  const memberToken = await login(member, memberName, memberPassword);
  await expect(nav(member, "用户管理")).toHaveCount(0);
  await member.evaluate(() => { location.hash = "#/users"; });
  await seen(member, "此页面需要管理员权限。");
  assert.equal((await read(memberToken, "/users")).status, 403);
  assert.equal((await read(memberToken, "/mcp/services")).status, 403);
  pass("keyboard login, invalid credentials, admin creation, role UI and direct 403");

  await nav(admin, "工具与 MCP").click();
  await expect(byName(admin, "MCP 服务")).toBeVisible();
  if (await control(admin, "发现 local 的工具").count() === 0) {
    await fill(admin, "服务键", "local");
    await click(admin, "登记 MCP 服务");
  }
  const multiply = "multiply_" + label;
  await register(admin, multiply, "read_only", "admin");
  await admin.getByText("试运行 " + multiply + " @1", { exact: true }).click();
  await fill(admin, "参数 JSON", '{"left":6,"right":7}');
  await click(admin, "执行只读工具");
  await expect(admin.locator("pre", { hasText: /"total": 42/ })).toBeVisible();
  assert.equal((await read(memberToken, "/tools/" + multiply + "/1/calls", {
    method: "POST", headers: { "Content-Type": "application/json" },
    body: '{"arguments":{"left":6,"right":7}}',
  })).status, 403);
  const reviewed = "reviewed_" + label;
  const registered = await register(admin, reviewed, "high", "member");
  await registered.getByRole("button", { name: "启用 Agent 执行", exact: true }).click();
  await expect(registered).toContainText("Agent 执行 已启用");
  const direct = await (await read(memberToken, "/tools")).json();
  const agentTools = await (await read(memberToken, "/tools?for_agent=true")).json();
  assert(!direct.some((tool) => tool.name === reviewed));
  assert(agentTools.some((tool) => tool.name === reviewed && tool.effect === "high"));
  pass("networkless MCP discovery, registration, live multiply, role and approval catalog");

  let release;
  const waitForGate = new Promise((resolve) => { release = resolve; });
  const routeKnowledge = async (route) => {
    await waitForGate;
    await route.continue();
  };
  const intercepted = member.waitForRequest((request) => request.url().includes("/knowledge-bases?"));
  await member.route("**/api/v1/knowledge-bases?*", routeKnowledge, { times: 1 });
  await nav(member, "知识库").click();
  await intercepted;
  await seen(member, "正在加载…");
  release();
  await seen(member, "暂无知识库。");
  await fill(member, "知识库名称", "公开样例 " + label);
  await click(member, "新建知识库");
  await expect(byName(member, "公开样例 " + label)).toBeVisible();
  const kbId = new URL(member.url()).hash.split("/").at(-1);
  assert.equal((await read(adminToken, "/knowledge-bases/" + kbId)).status, 404);
  async function upload(name, buffer) {
    await field(member, "文档文件").setInputFiles({ name, mimeType: "text/plain", buffer });
    await click(member, "上传并入库");
    await expect(byName(member, name)).toBeVisible();
  }
  await upload("malformed.txt", Buffer.from([0xff, 0xfe, 0xff]));
  const malformed = member.getByRole("heading", { name: "malformed.txt" }).locator("..");
  await expect(malformed.getByText("失败", { exact: true }).first()).toBeVisible({ timeout: 20000 });
  await click(member, "重试入库");
  await expect(malformed.getByText(/版本 2/).first()).toBeVisible({ timeout: 20000 });

  service("stop", "worker");
  await member.getByRole("link", { name: "公开样例 " + label, exact: true }).click();
  await upload("public-nova.txt", Buffer.from("The fictional Nova observatory opens at 17:45 every Thursday. The public marker is BLUE-NOVA."));
  await seen(member, "排队中");
  service("start", "worker");
  await seen(member, "可用", 45000);
  const download = member.waitForEvent("download");
  await click(member, "下载原文");
  assert.equal((await download).suggestedFilename(), "public-nova.txt");
  pass("read loading, empty, upload failure and retry, queued to ready, download and private API");

  await member.getByRole("link", { name: "在对话中使用" }).click();
  await fill(member, "会话标题", "P9 public chat");
  await click(member, "创建会话");
  await expect(field(member, "对话模式")).toHaveValue(kbId);
  await fill(member, "消息", "When does the fictional Nova observatory open?");
  const turn = member.waitForResponse((response) => response.url().endsWith("/rag?stream=true"));
  await click(member, "发送");
  assert.equal((await turn).status(), 200);
  await seen(member, "回答已写入会话历史。", 60000);
  await expect(member.locator(".assistant").last()).toContainText("17:45");
  await member.getByRole("link", { name: "定位引用片段", exact: true }).first().click();
  await expect(member.locator("#citation-target")).toContainText("BLUE-NOVA");

  await nav(member, "对话").click();
  await member.getByRole("link", { name: "P9 public chat", exact: true }).click();
  await expect(field(member, "对话模式")).toHaveValue("");
  await fill(member, "消息", "Reply with exactly P9-ONLINE.");
  await click(member, "发送");
  await seen(member, "回答已写入会话历史。", 60000);
  await expect(member.locator(".assistant").last()).toContainText("P9-ONLINE");

  await fill(member, "消息", "Write a list of 50 fictional star names, one at a time.");
  const stream = member.waitForResponse((response) => response.url().endsWith("/messages?stream=true"));
  await click(member, "发送");
  assert.equal((await stream).status(), 200);
  await click(member, "停止接收");
  await seen(member, "已保留输入。检查后台记录后，可按需再次发送。", 20000);
  await expect(field(member, "消息")).toHaveValue(/50 fictional/);
  await click(member, "读取会话历史");
  await expect(member.locator(".assistant").last()).toContainText("已取消", { timeout: 20000 });
  await fill(member, "消息", "Reply exactly P9-RECOVERED.");
  await click(member, "发送");
  await seen(member, "回答已写入会话历史。", 60000);
  await expect(member.locator(".assistant").last()).toContainText("P9-RECOVERED");
  pass("real RAG streaming and citation, chat streaming, intentional disconnect and reconciliation");

  await nav(member, "Agent").click();
  await member.getByRole("link", { name: "新建 Agent", exact: true }).click();
  await fill(member, "名称", "P9 public Agent");
  await fill(member, "系统提示词", "For requests to multiply, call the offered multiplication tool exactly once and return its output. If tool execution was rejected, explain that without another call.");
  await field(member, "工具版本").selectOption(reviewed + "@1");
  await click(member, "保存 Agent");
  await expect(byName(member, "运行 Agent")).toBeVisible();
  const agentId = new URL(member.url()).hash.split("/").at(-1);
  service("stop", "agent-worker");
  await fill(member, "运行指令", "Use the multiplication tool to compute 6 times 7.");
  await click(member, "发起运行");
  await member.waitForURL(/#\/agents\/run\//);
  const firstRun = new URL(member.url()).hash.split("/").at(-1);
  await seen(member, "排队中");
  await click(member, "暂停运行");
  await seen(member, "已暂停");
  await click(member, "恢复运行");
  await seen(member, "排队中");
  await expect(member.getByText(/已读取事件 #\d+/)).toBeVisible({ timeout: 10000 });

  const reconnect = member.waitForRequest((request) => request.url().includes("/events") &&
    Number(request.headers()["last-event-id"]) > 0);
  await memberContext.setOffline(true);
  service("stop", "api");
  await expect(member.getByText(/将从 #\d+ 重连/).first()).toBeVisible({ timeout: 15000 });
  service("start", "api");
  await memberContext.setOffline(false);
  assert(Number((await reconnect).headers()["last-event-id"]) > 0);
  service("start", "agent-worker");
  await expect(byName(member, "待审批工具调用")).toBeVisible({ timeout: 60000 });
  await expect(member.getByText("实际参数", { exact: true })).toBeVisible();
  const approval = (await (await read(memberToken, "/agent-runs/" + firstRun)).json()).approval_id;
  assert.equal((await read(adminToken, "/agent-runs/" + firstRun + "/approvals/" + approval)).status, 404);
  await click(member, "批准");
  await expect.poll(async () => (await (await read(memberToken, "/agent-runs/" + firstRun)).json()).status,
    { timeout: 60000 }).toBe("completed");
  const approved = await (await read(memberToken, "/agent-runs/" + firstRun)).json();
  const steps = await (await read(memberToken, "/agent-runs/" + firstRun + "/steps")).json();
  assert(steps.some((step) => step.tool_call_id));
  assert.match(approved.answer, /42/);

  await member.evaluate((id) => { location.hash = "#/agents/config/" + id; }, agentId);
  await fill(member, "运行指令", "Use the multiplication tool to compute 7 times 6.");
  await click(member, "发起运行");
  await member.waitForURL((url) => url.hash.startsWith("#/agents/run/") && !url.hash.endsWith(firstRun));
  const rejectedRun = new URL(member.url()).hash.split("/").at(-1);
  await expect(byName(member, "待审批工具调用")).toBeVisible({ timeout: 60000 });
  await click(member, "拒绝");
  await expect(member.getByText(/approval_decided/).first()).toBeVisible({ timeout: 20000 });
  await expect.poll(async () => (await (await read(memberToken, "/agent-runs/" + rejectedRun)).json()).status,
    { timeout: 60000 }).toBe("completed");
  const rejectedSteps = await (await read(memberToken, "/agent-runs/" + rejectedRun + "/steps")).json();
  assert(rejectedSteps.some((step) => step.status === "rejected" && step.error_code === "agent_approval_rejected"));
  service("stop", "agent-worker");
  await member.evaluate((id) => { location.hash = "#/agents/config/" + id; }, agentId);
  await fill(member, "运行指令", "Compute 7 times 6.");
  await click(member, "发起运行");
  await click(member, "取消运行");
  await seen(member, "已取消");
  service("start", "agent-worker");
  pass("real Agent approval and refusal, pause/resume/cancel, offline SSE cursor resume, private approval");

  await nav(member, "评测").click();
  await member.getByRole("link", { name: "创建评测集", exact: true }).click();
  await fill(member, "评测集名称", "public_" + label);
  await fill(member, "数据集版本", "v1");
  await click(member, "添加 RAG 样例");
  await fill(member, "样例键", "nova");
  await fill(member, "问题", "When does the fictional Nova observatory open?");
  await fill(member, "预期答案词（逐行）", "17:45");
  await field(member, "样例知识库").selectOption(kbId);
  await field(member, "金标原文").selectOption({ label: "public-nova.txt" });
  await click(member, "添加片段 1 为金标");
  await field(member, "已人工复核样例及预期结果").check();
  await click(member, "保存评测集版本");
  await expect(byName(member, "发起评测")).toBeVisible();
  const datasetHash = new URL(member.url()).hash;
  service("stop", "evaluation-worker");
  await fill(member, "配置版本", "cancel");
  await click(member, "运行评测");
  await seen(member, "批次结束后生成报告。");
  await click(member, "取消评测");
  await seen(member, "已取消");
  service("start", "evaluation-worker");
  const completed = [];
  for (const [configuration, k] of [["baseline", "2"], ["candidate", "1"]]) {
    await member.evaluate((hash) => { location.hash = hash; }, datasetHash);
    await fill(member, "配置版本", configuration);
    await fill(member, "Top-K（可选）", k);
    await click(member, "运行评测");
    await seen(member, "已完成", 80000);
    await seen(member, "通过");
    const id = new URL(member.url()).hash.split("/").at(-1);
    completed.push(id);
    const response = await read(memberToken, "/evaluations/runs/" + id + "/report");
    const report = await response.json();
    assert.equal(report.passed, 1);
    process.stdout.write("REPORT " + configuration + " passed=1 optional_token_total=" +
      (report.completion_tokens.total ?? "unreported") + "\n");
  }
  await member.getByText("人工复核 nova", { exact: true }).click();
  await fill(member, "复核说明", "Verified the public sample and its cited source.");
  await click(member, "保存复核");
  await seen(member, "人工复核 · 通过 · Verified the public sample and its cited source.");
  await nav(member, "评测").click();
  await field(member, "基线运行").selectOption(completed[0]);
  await field(member, "候选运行").selectOption(completed[1]);
  await click(member, "比较报告");
  await expect(byName(member, "报告比较")).toBeVisible();
  await expect(member.getByRole("table")).toContainText("100.0%");
  assert.equal((await read(adminToken, "/evaluations/runs/" + completed[0] + "/report")).status, 404);
  pass("queued evaluation and cancel, real cloud results, human review and A/B comparison");

  await nav(admin, "运行日志").click();
  await expect(byName(admin, "身份审计")).toBeVisible();
  const call = (await (await read(adminToken, "/tool-calls")).json()).find((row) => row.tool_name === multiply);
  await fill(admin, "请求 ID", call.request_id);
  await click(admin, "筛选请求 ID");
  await expect(admin.getByText(call.id, { exact: true })).toBeVisible();
  await nav(member, "运行日志").click();
  await expect(byName(member, "身份审计")).toHaveCount(0);
  await expect(member.getByRole("link", { name: /查看文档任务/ }).first()).toBeVisible();

  await member.setViewportSize({ width: 390, height: 844 });
  assert(await member.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
  await click(member, "退出登录");
  await expect(byName(member, "登录 AgentHub")).toBeVisible();
  assert.equal((await read(memberToken, "/auth/me")).status, 401);
  await admin.reload();
  await expect(byName(admin, "登录 AgentHub")).toBeVisible();
  assert.deepEqual(failures, []);
  pass("log filters, mobile width, server logout, in-memory login, no browser exceptions");
} finally {
  for (const name of stopped) service("start", name);
  await browser.close();
}
