import { segment } from "./api.js";
import { area, button, card, details, empty, field, form, h, info, json, link, localPanel, number, records, row, select, stack, status } from "./ui.js";

const ref = (item) => item.name + " @" + item.version;
const target = (item) => "/" + segment(item.name) + "/" + item.version;

function contracts(item) {
  return details("输入和输出约束", h("h3", {}, "输入"), json(item.input_schema), h("h3", {}, "输出"), json(item.output_schema));
}

export async function tools(ctx) {
  const items = await ctx.list("/tools");
  return stack(
    card("可用工具", records(items, (item) => {
      const result = h("div", { role: "status" });
      return [
        row(h("strong", {}, ref(item)), item.source, item.effect),
        h("p", {}, item.description), contracts(item),
        item.effect === "read_only" && details("试运行 " + ref(item),
          form(ctx, "执行只读工具", [
            area("参数 JSON", "arguments", { value: "{}", required: true, spellcheck: "false" }),
          ], async (data) => {
            result.replaceChildren();
            let args;
            try {
              args = JSON.parse(data.arguments);
              if (!args || typeof args !== "object" || Array.isArray(args)) throw new Error();
            } catch {
              throw new Error("参数必须为 JSON 对象。请参照输入约束。");
            }
            const output = await ctx.api("/tools" + target(item) + "/calls", { method: "POST", data: { arguments: args } });
            result.append(h("p", {}, "调用 ID " + output.call_id), json(output.result), link("查看工具调用记录", "logs"));
          }), result),
      ];
    }, "当前没有可用工具。")),
    ctx.user.role_name === "admin" ? await mcp(ctx) : card("MCP 管理", empty("登记和启停服务及工具需要管理员权限。")),
  );
}

async function mcp(ctx) {
  const [services, registrations] = await Promise.all([ctx.api("/mcp/services"), ctx.list("/tool-registrations")]);
  const create = form(ctx, "登记 MCP 服务", [
    field("服务键", "key", { required: true, pattern: "[a-z][a-z0-9_\\-]{0,39}", placeholder: "例如 local" }),
  ], async (data) => {
    await ctx.api("/mcp/services", { method: "POST", data });
    ctx.reload();
  });

  function toggle(label, path, enabled) {
    return button(label, ctx, async () => {
      await ctx.api(path, { method: "PATCH", data: { enabled } });
      ctx.reload();
    });
  }

  return stack(
    card("MCP 服务", h("p", {}, "服务键对应部署端预设的地址与凭据。"), create,
      records(services, (service) => {
        const path = "/mcp/services/" + segment(service.key);
        const holder = h("div");
        const discover = localPanel(ctx, holder, async () => {
          const catalog = await ctx.api(path + "/tools");
          return records(catalog, (definition) => registration(ctx, service, definition), "服务未返回可注册工具。");
        });
        return [
          row(h("strong", {}, service.key), service.transport, status(service.enabled ? "enabled" : "disabled")),
          row(toggle(service.enabled ? "停用服务" : "启用服务", path, !service.enabled),
            service.enabled && button("发现 " + service.key + " 的工具", ctx, discover)),
          holder,
        ];
      }, "暂无已登记 MCP 服务。")),
    card("工具注册", records(registrations, (item) => {
      const path = "/tool-registrations" + target(item);
      return [
        row(h("strong", {}, ref(item)), status(item.enabled ? "enabled" : "disabled"), "Agent 执行 " + (item.agent_enabled ? "已启用" : "已停用")),
        info([["服务", item.service_key], ["远端名称", item.remote_name], ["所需角色", item.required_role], ["副作用", item.effect], ["定义摘要", item.definition_digest]]),
        contracts(item), row(item.effect === "read_only" && toggle(item.enabled ? "停用工具" : "启用工具", path, !item.enabled),
          item.effect !== "read_only" && toggle(item.agent_enabled ? "停用 Agent 执行" : "启用 Agent 执行", path + "/agent-execution", !item.agent_enabled)),
      ];
    }, "暂无已登记 MCP 工具。")),
  );
}

function registration(ctx, service, definition) {
  return stack(
    h("h3", {}, definition.name), h("p", {}, definition.remote_description),
    info([["只读提示", String(definition.read_only_hint ?? "未知")], ["破坏性提示", String(definition.destructive_hint ?? "未知")],
      ["定义摘要", definition.definition_digest]]),
    contracts(definition),
    details("注册此工具", form(ctx, "注册工具", [
      field("平台工具名称", "name", { required: true, pattern: "[a-z][a-z0-9_\\-]{0,63}" }),
      number("版本", "version", 1, 1, 1000000),
      area("已复核的说明", "description", { required: true, maxlength: 500 }),
      select("所需角色", "required_role", [["admin", "管理员"], ["member", "成员"]]),
      select("副作用", "effect", [["", "请选择"], ["read_only", "只读"], ["write", "写入"], ["high", "高风险"]], { required: true }),
      number("超时秒数", "timeout_seconds", 10, 1, 60),
      field("我已复核输入输出约束、角色和副作用", "reviewed", { type: "checkbox", required: true }),
    ], async ({ name, version, description, required_role, effect, timeout_seconds }) => {
      await ctx.api("/tool-registrations", { method: "POST", data: {
        name, version: Number(version), service_key: service.key, remote_name: definition.name,
        definition_digest: definition.definition_digest, description, required_role, effect,
        timeout_seconds: Number(timeout_seconds),
      } });
      ctx.reload();
    })),
  );
}
