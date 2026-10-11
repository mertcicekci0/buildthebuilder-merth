"""Static checks on a generated workflow before it is sent to Freya. Deterministic: no model call.

Each check comes from a documented or verified platform rule (docs/*.md, probes/README.md).
"""
import json
import re


def _walk(x, path=""):
    if isinstance(x, dict):
        for k, v in x.items():
            yield from _walk(v, f"{path}/{k}")
    elif isinstance(x, list):
        for i, v in enumerate(x):
            yield from _walk(v, f"{path}[{i}]")
    else:
        yield path, x


def code_snippets(wf):
    """(where, source) for every code node, code_exec tool and hook."""
    out = []

    def tools_of(node):
        return (node.get("tools") or []) + (node.get("pre_actions") or [])
    for name, n in wf["nodes"].items():
        for t in tools_of(n):
            fn = t.get("function", {})
            if fn.get("handler") == "__function__:code_exec":
                out.append((f"{name}/{fn.get('name')}", fn["parameters"]["properties"]["code"]["default"]))
            for h in ("pre_process_code", "post_process_code"):
                if t.get(h):
                    out.append((f"{name}/{fn.get('name')}/{h}", t[h]))
    for t in wf.get("tools", []):
        for h in ("pre_process_code", "post_process_code"):
            if t.get(h):
                out.append((f"tools/{t['function']['name']}/{h}", t[h]))
    return out


def lint(wf, api=None):
    problems = []
    nodes = wf.get("nodes", {})
    if wf.get("initial_node") not in nodes:
        problems.append("initial_node missing")
    for path, v in _walk(wf):
        if v is None:
            problems.append(f"null value at {path}")
    targets_ok = set(nodes)   # __previous__ only in global nodes (kit docs/workflow-format.md transition table)
    for name, n in list(nodes.items()) + [(k, g) for k, g in (wf.get("global_nodes") or {}).items()]:
        tg = [tr.get("target_node") for tr in n.get("transitions", [])]
        dup = sorted({x for x in tg if tg.count(x) > 1})
        if dup:   # the engine ORs same-target transitions at load time (workflow-format.md, routing step 2)
            problems.append(f"{name}: several transitions to {dup} are merged into one by the engine")
    for name, n in nodes.items():
        t = n.get("node_type")
        if n.get("name") != name:
            problems.append(f"{name}: name differs from key")
        if t != "conversation" and n.get("tools"):
            problems.append(f"{name}: tools on a {t} node are stripped by the platform")
        if t == "conversation" and n.get("pre_actions"):
            problems.append(f"{name}: pre_actions on a conversation node are stripped")
        for tr in n.get("transitions", []):
            if tr.get("target_node") not in targets_ok:
                problems.append(f"{name}: transition to unknown node {tr.get('target_node')}")
            cond = tr.get("condition", {})
            if cond.get("logic") == "and":
                problems.append(f"{name}: top-level AND condition")
        if n.get("passthrough"):
            trs = n.get("transitions", [])
            if not trs or trs[-1].get("condition", {}).get("operator") != "always":
                problems.append(f"{name}: passthrough node must end with an always transition")
        if t == "end_call":
            if n.get("transitions"):
                problems.append(f"{name}: end_call node with transitions")
            if not n.get("terminal"):
                problems.append(f"{name}: end_call node without terminal")
        if n.get("max_node_turns") and n.get("max_turn_transition") not in nodes:
            problems.append(f"{name}: max_node_turns without a valid max_turn_transition")
        if t == "conversation":
            names = [x.get("function", {}).get("name") for x in n.get("tools", [])]
            if len(names) != len(set(names)):
                problems.append(f"{name}: duplicate tool names {names}")
    # reachability of an end node from every node
    graph = {k: {tr["target_node"] for tr in n.get("transitions", []) if tr.get("target_node") in nodes} |
             ({n["max_turn_transition"]} if n.get("max_turn_transition") in nodes else set()) for k, n in nodes.items()}
    ends = {k for k, n in nodes.items() if n.get("node_type") == "end_call"}
    for k in nodes:
        seen, stack = set(), [k]
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack += list(graph.get(x, ()))
        if not (seen & ends):
            problems.append(f"{k}: cannot reach an end_call node")
    reach, stack = set(), [wf.get("initial_node")]
    while stack:
        x = stack.pop()
        if x in reach or x not in nodes:
            continue
        reach.add(x)
        stack += list(graph[x])
    for k in nodes:
        if k not in reach:
            problems.append(f"{k}: unreachable from initial node")
    for where, src in code_snippets(wf):
        if "{{" in src or "}}" in src:
            problems.append(f"{where}: code contains a double brace")
        try:
            compile(src, where, "exec")
        except SyntaxError as e:
            problems.append(f"{where}: syntax error {e}")
        if re.search(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", src):   # an escape lost in a non-raw template (e.g. a regex \b became a backspace)
            problems.append(f"{where}: control character in generated code")
    if api:
        problems += contract_problems(wf, api)
    return problems


def never_sent(tool):
    """True when the tool's pre-hook ends, at top level, with an unconditional raise (request is never sent)."""
    import ast
    src = tool.get("pre_process_code") or ""
    try:
        body = ast.parse(src).body
    except SyntaxError:
        return False
    return bool(body) and isinstance(body[-1], ast.Raise)


def contract_problems(wf, api):
    """Every api_call must target a tool in the API with fields and enum values the API allows."""
    out = []
    by_url = {c["url"]: c for c in api["tools"].values()}

    def check(where, tool):
        fn = tool.get("function", {})
        if fn.get("handler") != "__function__:api_call":
            return
        if never_sent(tool):
            return  # local oracle: its pre-hook always ends in raise, so the request is never sent
        props = fn["parameters"]["properties"]
        url = props.get("url", {}).get("default")
        c = by_url.get(url)
        if not c:
            out.append(f"{where}: url {url} is not an endpoint of the tool API")
            return
        fields = {f["name"]: f for f in c["fields"]}
        body = props.get("body", {}).get("properties", {})
        for k, leaf in body.items():
            if k not in fields:
                out.append(f"{where}: body field {k} not in API for {c['name']}")
                continue
            d = leaf.get("default")
            if fields[k].get("enum") and isinstance(d, str) and "{{" not in d and d not in fields[k]["enum"]:
                out.append(f"{where}: {k}={d} not in API enum {fields[k]['enum']}")
        for f in c["fields"]:
            if f["required"] and f["name"] not in body:
                out.append(f"{where}: required field {f['name']} of {c['name']} missing")
    for name, n in wf["nodes"].items():
        for t in (n.get("tools") or []) + (n.get("pre_actions") or []):
            check(name, t)
    for t in wf.get("tools", []):
        check("tools", t)
    return out


def stats(wf):
    nodes = wf["nodes"]
    tools = sum(len(n.get("tools") or []) + len(n.get("pre_actions") or []) for n in nodes.values()) + len(wf.get("tools", []))
    return {"steps": len(nodes), "tools": tools, "bytes": len(json.dumps(wf))}
