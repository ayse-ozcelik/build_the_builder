CONTEXT — Freya × YC "Build the Builder" hackathon

Read this file first. It carries over everything learned so far.
Last updated: Sat 2026-10-10, after reading kit v3 (kit/README.md, kit/docs/, kit/freya.py, kit/tools/run_cases.py, kit/examples/agent_builder.py).
Where this file and the kit disagree, the kit wins. Lines marked (event page) come from the earlier chat and are not repeated in the kit.

How to work with me (the user)
I am new to this platform. Explain simply, step by step. Answer in Turkish.
Before replying, translate my prompt to English, treat the English version as the prompt, and show it in one line as Prompt (EN): ....
Before doing anything I ask, first say whether it is allowed by the hackathon rules (✅ allowed / ⚠️ careful / ❌ forbidden) and why, in one or two lines. Only then do it. Never do something rule-sensitive without telling me.
Never put API keys in code, chat or commits. Keys live in .env (project root), and .env is in .gitignore. I paste the keys myself. Never read or print .env.
Ask before running commands.

Project layout (root = C:\Users\aayse\deneme1\builder, its own git repo)
builder/      our builder code (trace.py, tools_gen.py so far)
kit/          the organizers' kit v3, unpacked as-is. Do not edit it.
samples/      sample_openapi.json (hand-made from reference_workflow.json, for tests only)
tests/        test_prep.py (python tests\test_prep.py, 20 checks)
traces/       one trace per builder run
submission/   exported workflows for the final
reference_workflow.json   export of the sample workflow from the workspace (same agent as kit/examples/base-agent.json)
.env          keys, never committed

The task
Event: https://www.freyavoice.ai/hackathon — online, Oct 10–11 2026, solo or team of 2. 1st place: YC interview. (event page)
We write a builder: a program that reads a bank's brief files + the bank's tool API (openapi.json) and creates a phone agent (a Freya workflow) with no manual step.
Use case: debt collection calls for "Freya Bank". The agent verifies identity, states what is owed, records a payment promise, or handles dispute / callback / transfer / wrong person, then closes.
Only the generated phone agents are scored: share of hidden customers handled. No partial credit.
A customer counts as handled when all three hold:
(1) Outcome: the bank's records are right — the outcome, every promise (product, amount, date), every note, callback and transfer. Read from the bank's ledger, not from what the agent said.
(2) Policy: the conversation stayed inside the brief (e.g. no debt mentioned before the identity check, nothing told to a relative).
(3) Honesty: never says "recorded" when the tool call failed.
A judge model reads the transcript for 2 and 3 against the checks listed for that customer.
The same agent can pass a customer in one run and fail the next. One green run is a hint, not proof.
Tie-breaks: total promised amount on passed customers → shorter build time → earlier submission.
Calls are English text. The agent gets the call date/time as call variable now. Use that, not the system clock variables.

The final
Hidden briefs: same kind of call, different rules — other numbers and limits, other identity questions, another outcome table, another calendar. One is a clean spec; the other is raw bank material (a mail, a policy extract, a memo that changes it, a code list).
Each final brief comes with its own tool API file. The 7 tool names stay fixed: verify_identity, get_account, create_promise, add_note, schedule_callback, transfer_to_agent, close_call. Fields and allowed values can change.
Final customers can have up to three products (as in Brief B).
Tested on unseen customers, on several calendar days, three times each; a customer counts only when all three runs pass. (Event page said 60 cases = 2 briefs × 30 customers; the kit does not give a number.)
Final customers talk like people: numbers in two parts, corrections, off-topic talk, questions back, amounts/dates in words. Practise with run_cases.py --style pieces | corrects | talkative | asks_back | in_words.
"Build something that reads a brief, not something that knows this one."

From the organizers' meeting (my notes, added 2026-10-10)
Today: learn by building one good phone agent by hand. Write down what works as reusable "skill" files. Tomorrow the builder uses those skills.
Hidden briefs will be very messy: meeting notes, e-mails, records, Slack messages.
Test customers will be difficult: angry, fraudsters, people trying prompt injection.
Rules like "do not do Y before X" must be guaranteed by the workflow structure (nodes and transitions on tool results), not by the prompt alone.
Sunday 17:00–20:00 UTC there will be a test limit on the platform; local testing is unlimited. The builder should be able to simulate its own customer personas locally and test its own agent (with the chat API).
Build speed matters.
Hidden briefs may be banking or insurance, not only collections. Do not hardcode collections-specific things. (The kit README says "the same kind of call with different rules" and that the 7 tool names stay fixed; treat the domain as possibly different anyway and take everything from the brief and the tool API file.)
The builder must be reproducible. Skills and traces are shared with the submission.

Rules that matter
Builder takes brief files as input and creates the agent with no manual step. Never edit an agent by hand afterwards.
Builder may use any model / coding agent. The phone agent runs on the model set in kit/freya.py (AGENT_LLM = experimental-5). Do not change it.
The agent may call only the bank's tool API. Code steps and hooks inside the workflow are allowed and expected.
After the final briefs open (Sun 17:00 UTC = 20:00 Istanbul, event page): only bug fixes. Nothing from a final brief may be written into the builder (no rule, value, name, holiday date, outcome name). Fixing a crash on an unexpected table is fine. Do not paste final briefs into a coding session to tune the builder.
Run the builder once per final brief. You may run it more than once and submit the agent of one run, with that run's trace.
Builder run ≤ 30 min per brief, including its own tests.
Workflow limit: the kit README says 200 per workspace, but I (the user) see a limit of 10. Treat it as 10. Never create a new workflow for an experiment.
All further work goes into ONE workflow, named "bank collections" (id fa765caf-987d-4d19-8353-4611cbb0bdd4, agent dadb25c6-fec0-41b0-8874-b1644dda388a). Update it in place with freya.save_workflow("bank-collections", workflow), run from kit/ (the slot is in kit/.freya-agents.json).
Other workflows in the workspace (as of 2026-10-10): "Freya Bank Collections Base (2 nodes)" (came with the workspace), "base ..." (kit's example agent, 0/10), "brief-a ..." (agent made by the kit's example builder, 8/10 with --reps 3; reference, do not overwrite).
One simulation at a time per workspace. A ten-customer run takes 3–11 min; a second run_cases.py waits.
Trace per run: start and end time, each model call (model + one-line purpose), what it created on Freya, any test it ran on its own agent. Plain text, no fixed format. One trace per final brief.
Submit per final brief: exported workflow via freya.export_workflow(workflow_id, "<brief>.workflow.json") (organizers load that file into their own workspace, so the export is what is scored), the trace, the code (repo + commit). Deadline Sun 20:00 UTC = 23:00 Istanbul (event page).
Top 10 builders are re-run by the organizers; a clearly lower score removes the entry. (event page)
All data is synthetic. No real customer data.

Kit v3 (in kit/)
README.md                       rules, ten-minute start, troubleshooting
briefs/brief-a.md               47 rules, one product
briefs/brief-b.md               extra rules for up to 3 products; builds on Brief A, so pass both files
tools/openapi.json              the bank's tool API
public-test-set.json            13 practice customers (--brief A runs ten of them)
examples/base-agent.json        example AGENT: 2 steps, 4 of 7 tools, passed 2 of 13. Same as reference_workflow.json
examples/agent_builder.py       example BUILDER on the Claude Agent SDK (replaces minimal_builder.py). Got 10/10 on Brief A in one run
.claude/skills/build-phone-agent/SKILL.md   the one skill the example builder uses
freya.py                        Freya API client, stdlib only
tools/run_cases.py, score.py    run practice customers and score them
docs/workflow-format.md, tools-and-code.md, api.md
env.example                     template for the keys

Keys (environment variables, read by freya.py at import)
FREYA_API_KEY          starts with fak_, from the Freya workspace under API keys
FREYA_API_URL          https://staging-app.freyavoice.ai  (staging, not app.freyavoice.ai)
FREYA_BANK_TEAM_KEY    a secret of 16+ characters we make up; the same one every time
The example builder also needs a Claude login or ANTHROPIC_API_KEY.
The kit says "cp env.example env.sh; source env.sh". We keep them in .env at the project root instead (KEY=value lines) and load it in the shell before each command:
  Git Bash:  set -a; . ./.env; set +a
Windows notes: use python, not python3. Set PYTHONUTF8=1 so files are read as UTF-8. Run kit commands from inside kit/ (state files .freya-agents.json, .personas.json, results/, traces/ are written relative to it).

Kit commands (from kit/)
python -c "import freya,json; print(freya.save_workflow('base', json.load(open('examples/base-agent.json'))))"   → {"workflow_id", "agent_id"}
python tools/run_cases.py <agent_id> --brief A [--reps 3] [--style talkative] [--only id1,id2] [--out results/run.json]
python examples/agent_builder.py briefs/brief-a.md                      (needs pip install claude-agent-sdk)
python examples/agent_builder.py briefs/brief-a.md briefs/brief-b.md
run_cases.py prints per customer: PASS/FAIL, the tool calls made, the failure reason; then the slowest agent turn (warning when the median is above 10 s = the model was busy, the score is too low). Every run is appended to results/log.jsonl.

Freya API (docs/api.md)
Base https://staging-app.freyavoice.ai, header Authorization: Bearer fak_..., and a browser-like User-Agent (otherwise HTML "error code: 1010"). freya.py sets it.
Responses: {success, data: {message, result}}; payload is data.result.
POST /api/v2/workflow {name, workflow} creates workflow + agent (result.workflow.id, .agentId). ?dryRun=true validates without saving.
PUT /api/v2/workflow/{id} replaces the whole definition and does NOT publish. Simulations run the published version. freya.save_workflow / update_workflow update and publish.
POST /api/v2/chat/message {workflowId, message, customerVariables, callId?} → response, callId. One turn in seconds; fastest self-test. May skip a first code step; long turns can 504.
GET /api/v2/call/{callId} → messages with system events: node_enter, function_call_start (full request), function_call_complete, route_evaluated (every branch + variable_table), error. Use route_evaluated to see why a transition did or did not fire.

Freya workflow format (docs/workflow-format.md, docs/tools-and-code.md)
Top level: initial_node, nodes{}, global_nodes{}, system_prompt (shared by every node), test_variables, tools[] (lifecycle tools only).
agent_config: LEAVE IT OUT of what we send (earlier note "copy verbatim" was wrong). The platform fills defaults and freya.update_workflow sets the event model. A workflow that cannot start a call: try without agent_config.
No null values anywhere: the schema is strict. Omit optional fields or use "". reference_workflow.json is an export and contains nulls, so do not copy its nodes wholesale.
Three separate model calls per turn: extraction → routing → dialogue. Routing sees only the transitions, never task_message. The dialogue model sees system_prompt + role_message + task_message + history + the node's tools.
Node types: conversation (task_message, tools, extraction_schema, transitions), api_request (pre_actions, node-level response_mapping), code (pre_actions with code_exec), end_call (terminal).
The platform silently strips tools from non-conversation nodes and pre_actions from conversation nodes. A fixed call before a conversation needs its own api_request node in front.
on_enter: {"type":"tts","text":...} fixed line ({{var}} works), {"type":"llm"} default, {"type":"silent"}. on_enter_first_entry_only stops a repeated opening line.
Variables: one table for the whole call, never cleared. {{var}} works in prompts, on_enter text, URLs, bodies. An unresolved {{var}} in a prompt stays as literal text.
Transitions: checked in list order, deterministic first. Operators equals, not_equals, greater_than, less_than, contains, is_set, is_not_set, always, matches_intent. Variable checks go in {"logic":"or","conditions":[...]}; nest "and" inside; never "and" at top level.
Routing runs again right after a tool result, so a transition on a result variable fires in the same turn.
Do not route on a boolean: they arrive as strings. Test both "true" and "True", or route on a string we write ourselves.
Passthrough nodes (api_request, code): passthrough true, passthrough_default_transition, on_enter silent, and a last always transition — otherwise deadlock.
max_node_turns default is 10; raise it (60–80) on nodes that hold a long conversation.
extraction_schema: min_confidence defaults to 0.95 (too strict; set ~0.7), use type "date" for dates. To fork on what the customer wants: extract an enum, branch with equals.
context_variables is unreliable (dropped on save). Put values into prompts with {{var}}.
Ending: an end_call node with a terminal function; call.endedReason is the terminal's function.name. (base-agent instead uses an end_call tool with trigger_pattern "Have a good day".)

Tools
Bank tool shape (generate mechanically from openapi.json, tools_gen.py does this):
{"type":"function","function":{"name":..., "handler":"__function__:api_call", "description":...,
  "parameters":{"type":"object","properties":{
    "url":{"type":"string","default":"<server url>/<tool>"},
    "method":{"type":"string","default":"POST"},
    "headers":{"type":"object","properties":{"Content-Type":{"type":"string","default":"application/json"}}},
    "body":{"type":"object","required":[...],"properties":{ "<field>":{"type":..,"default":"{{<field>}}","description":..,"enum":[..]}, "customer_id":{"type":"string","default":"{{customer_id}}"} }}}}},
 "config":{"timeoutMs":12000,"maxRetries":0}}
How a field gets its value: default with a literal or a {{var}} that HAS a value on node entry → baked by the workflow, hidden from the model. default with a {{var}} that has NO value → the model fills it (sees type, description, enum). Never use const.
So the panel's "unresolved variables" warning for {{field}}, {{answer}}, {{date}}, {{amount}}, {{product_id}} is expected: those are the model-filled fields. (Open question from the earlier chat: answered.)
Careful: a model-filled field must not share its name with a variable that is already set, or it is baked and the model never sees it.
After a call: <tool>_args.<field>, <tool>_response.status / .data.<key> / .status_code, <tool>_success_count, <tool>_failure_count. response_mapping paths start with "data.".
Tool description decides whether the model calls it: write the trigger ("CALL it every time ...").
maxRetries 0 on tools that write (a retried write can record twice).
Hooks on api_call tools: pre_process_code (vars, config) and post_process_code (result = envelope; API body is result["data"]). Wrap in try/except.
Known failure: the agent says nothing after a tool result, or ignores a negative result. Fix that worked: a post-hook that adds a what_to_do_next sentence into result["data"] and removes result["header"]; and the prompt says "after every tool result, speak or call the next tool".
Code nodes: read/write through vars, never raise, no {{ or }} in the code (also not a dict literal ending in }}), write strings for routing flags. json, re, datetime are available. Do date and amount arithmetic in code and hand the model finished facts as text.
Lifecycle tools: workflow-level tools[] with config.alwaysRunsAt ["start_of_call" | "end_of_call"]; never shown to the model; an unresolved {{var}} is filled by one model call. close_call in the base agent is an end_of_call tool.
Product ids come from get_account (p1, p2, ...). The model invents ids otherwise: give them in the tool result or fill them with code.

Troubleshooting (README)
"error code: 1010" → missing browser-like User-Agent.
"Workspace plan limit reached" → too many workflows; update in place.
Score does not move after a change → the update was not published; check agentVersionNumber.
Calls end as customer-ended-call → the agent was silent too long.
"unknown product_id" → the model made an id up.
Simulation stays "running" with no call → the workflow cannot start; try one chat turn; leave agent_config out.

Planned builder architecture
extract.py — brief files → structured spec JSON via LLM (identity checks, disclosure rules, promise date/amount rules, holidays, outcomes table, multi-product rules, closing). For raw-material briefs: a later memo overrides earlier policy.
tools_gen.py — openapi.json → Freya tool definitions (never hardcode field names/enums). Done against the sample; next: run on kit/tools/openapi.json.
compile.py — spec → workflow JSON from a fixed skeleton (right party → identity → disclose → negotiate → promise/dispute/callback/transfer → close); LLM writes per-node prompts; date/amount checks in code nodes and tool hooks, not in the model (pre_actions do not exist on conversation nodes); no agent_config; no nulls.
selftest.py — short self-test loop (chat API or run_cases), patch prompts, stay within 30 min.
trace.py — logs everything required for submission. Done.
build.py — one command: python builder/build.py <brief files...> <tool_api_path>.
Alternative to weigh: start from the kit's agentic builder (Claude Agent SDK + skill) and improve the skill, instead of a hand-written pipeline.

Robustness plan: create our own mutated briefs (different identity questions, holidays, limits, outcome table, and a "messy mail + memo" version of brief A) to make sure the builder generalizes.

Timeline (Istanbul time)
Sat: explore kit, baseline with the example agent and example builder, builder v1.
Sun until 20:00: Brief B, robustness with mutated briefs, determinism (--reps 3), styles, dry run of full submission.
Sun 20:00–23:00: run builder on final briefs, fix crashes only, export, commit, submit.
