import test from "node:test";
import assert from "node:assert/strict";

import { BACKENDS, validateCatalog, availableTools } from "../src/catalog.js";
import { handleAuthenticatedMcp, validateArguments } from "../src/mcp.js";

const BASE = "https://mcp.example.invalid/mcp";
function request(method, params = {}, options = {}) {
  return new Request(BASE, {
    method: "POST",
    headers: { "Content-Type": "application/json", ...options.headers },
    body: JSON.stringify({ jsonrpc: "2.0", id: 7, method, params }),
  });
}
const single = (overrides = {}) => [{ ...BACKENDS[0], enabled: true, ...overrides }];

test("catalog is vendor-neutral and all entries default deny", () => {
  assert.equal(validateCatalog(), true);
  assert.equal(BACKENDS.every((b) => !b.enabled), true);
  assert.deepEqual(availableTools({ ATLAS_VPC: { fetch() {} } }), []);
  const added = [...BACKENDS, {
    id: "new-independent-toolset", owner: "example/project",
    binding: "NEW_BACKEND", origin: "http://backend.internal/mcp",
    enabled: false, tools: [],
  }];
  assert.equal(validateCatalog(added), true);
});

test("rejects invalid tool catalog and arbitrary remote origins", () => {
  assert.throws(() => validateCatalog(single({ origin: "https://evil.invalid/mcp" })), /origin/);
  assert.throws(() => validateCatalog(single({ origin: "http://user@evil.invalid/mcp" })), /origin/);
  assert.throws(() => validateCatalog(single({ binding: "env.SSH_KEY" })), /binding/);
  assert.throws(() => validateCatalog(single({ tools: [{ ...BACKENDS[0].tools[0], name: "tool/exec" }] })), /tool/);
  assert.throws(() => validateCatalog([...single(), ...single()]), /backend-id/);
});

test("initialization succeeds without registering any backend", async () => {
  const response = await handleAuthenticatedMcp(request("initialize", {protocolVersion:"2025-11-25"}), {});
  assert.equal(response.status, 200);
  const payload = await response.json();
  assert.equal(payload.result.serverInfo.name, "Truthrail Tools");
  assert.equal(payload.result.protocolVersion, "2025-11-25");
  assert.deepEqual(payload.result.capabilities, { tools: {listChanged:false} });
});

test("only the canonical /mcp path is eligible for tool calls", async () => {
  const other = new Request(BASE + "/extra", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ jsonrpc: "2.0", id: 7, method: "tools/list" }),
  });
  const response = await handleAuthenticatedMcp(other, {});
  assert.equal(response.status, 404);
});

test("disabled backends are not listed even with VPC bindings", async () => {
  const response = await handleAuthenticatedMcp(request("tools/list"), {
    ATLAS_VPC: { fetch: async () => { throw Error("must not be called"); } },
  });
  assert.deepEqual((await response.json()).result.tools, []);
});

test("unknown method, malformed JSON, cross-origin request and GET fail closed", async () => {
  const unsupported = await handleAuthenticatedMcp(request("powershell/exec"), {});
  assert.equal((await unsupported.json()).error.code, -32601);
  const malformed = await handleAuthenticatedMcp(
    new Request(BASE, {method:"POST",headers:{"Content-Type":"application/json"},body:"{"}), {}
  );
  assert.equal(malformed.status, 400);
  const crossOrigin = await handleAuthenticatedMcp(request("tools/list",{}, {headers:{Origin:"https://evil.example"}}), {});
  assert.equal(crossOrigin.status, 403);
  const get = await handleAuthenticatedMcp(new Request(BASE), {});
  assert.equal(get.status, 405);
  const notification = await handleAuthenticatedMcp(
    new Request(BASE,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify({
      jsonrpc:"2.0",method:"notifications/initialized"
    })}), {}
  );
  assert.equal(notification.status, 202);
});

test("tool arguments reject unexpected properties, non-integers and oversized inputs", () => {
  const schema = BACKENDS[0].tools.find(x=>x.name==="search_rulers").inputSchema;
  assert.equal(validateArguments({text:"Ленин",limit:5},schema), true);
  assert.equal(validateArguments({text:"a".repeat(201)},schema),false);
  assert.equal(validateArguments({unexpected:"value"},schema),false);
  assert.equal(validateArguments({limit:1.25},schema),false);
  assert.equal(validateArguments({__proto__: null, extra:"no"},schema),false);
  const compareSchema=BACKENDS[0].tools.find(x=>x.name==="compare_rulers").inputSchema;
  assert.equal(validateArguments({person_ids:["person-p03"]},compareSchema),true);
  assert.equal(validateArguments({person_ids:["x".repeat(91)]},compareSchema),false);
  assert.equal(validateArguments({person_ids:[]},compareSchema),false);
});

test("only explicitly enabled and VPC-bound tools appear", async () => {
  const backends = single();
  const env={ATLAS_VPC:{fetch: async () => { throw Error("not for list"); }}};
  const response=await handleAuthenticatedMcp(request("tools/list"),env,backends);
  assert.deepEqual((await response.json()).result.tools.map(x=>x.name),
    BACKENDS[0].tools.map(x=>x.name));
  const noBinding=await handleAuthenticatedMcp(request("tools/list"),{},backends);
  assert.deepEqual((await noBinding.json()).result.tools,[]);
  const denied=await handleAuthenticatedMcp(request("tools/call",{
    name:"shell",arguments:{command:"id"}
  }),env,backends);
  assert.equal((await denied.json()).error.message,"Tool not enabled");
});

test("real MCP sequence delegates only to configured private binding", async () => {
  const requests=[];
  const fakeBinding={
    async fetch(url,init){
      assert.equal(url,"http://atlas.internal/mcp");
      assert.equal(init.method,"POST");
      assert.equal(init.redirect,"manual");
      const req=JSON.parse(init.body);
      requests.push({method:req.method,session:init.headers.get("Mcp-Session-Id"),req});
      if(req.method==="initialize") return Response.json({
        jsonrpc:"2.0",id:1,result:{protocolVersion:"2025-11-25",
          capabilities:{tools:{}},serverInfo:{name:"atlas",version:"1"}}
      },{headers:{"Mcp-Session-Id":"session123"}});
      if(req.method==="notifications/initialized")return new Response(null,{status:202});
      if(req.method==="tools/call")return Response.json({
        jsonrpc:"2.0",id:2,
        result:{content:[{type:"text",text:JSON.stringify({found:true,source_ids:["PC03"]})}],isError:false}
      });
      throw Error("unexpected");
    }
  };
  const response=await handleAuthenticatedMcp(request("tools/call",{
    name:"get_evidence",arguments:{record_id:"tenure-p03"},
  }),{ATLAS_VPC:fakeBinding},single());
  const body=await response.json();
  assert.equal(body.result.isError,false);
  assert.match(body.result.content[0].text,/PC03/);
  assert.deepEqual(requests.map(x=>x.method),[
    "initialize","notifications/initialized","tools/call"
  ]);
  assert.equal(requests[1].session,"session123");
  assert.equal(requests[2].session,"session123");
  assert.deepEqual(requests[2].req.params.arguments,{record_id:"tenure-p03"});
});

test("backend failure and redirect never leak private error bodies", async () => {
  let count=0;
  const env={ATLAS_VPC:{async fetch(){
    count++;
    return new Response("https://atlas.internal/secret/api?token=private",{status:302});
  }}};
  const response=await handleAuthenticatedMcp(request("tools/call",{
    name:"dataset_info",arguments:{}
  }),env,single());
  assert.equal(count,1);
  const payload=await response.json();
  assert.equal(payload.result.isError,true);
  assert.match(payload.result.content[0].text,/temporarily unavailable/);
  assert.doesNotMatch(JSON.stringify(payload),/atlas\.internal|token=private/);
});

test("unknown tool, invalid data and disabled backend never touch network", async () => {
  let calls=0;
  const env={ATLAS_VPC:{async fetch(){calls++;throw Error("must not call");}}};
  const rejected=await handleAuthenticatedMcp(request("tools/call",{
    name:"compare_rulers",arguments:{person_ids:[42]}
  }),env,single());
  assert.equal((await rejected.json()).error.code,-32602);
  const disabled=await handleAuthenticatedMcp(request("tools/call",{
    name:"dataset_info",arguments:{}
  }),env,BACKENDS);
  assert.equal((await disabled.json()).error.code,-32602);
  assert.equal(calls,0);
});
