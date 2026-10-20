# Interview Notes

## One-line description

A LangGraph agent that investigates failed data pipeline runs: it picks which systems to check with tools, follows the evidence, finds the fix in runbooks and past incidents, and returns a structured diagnosis, with human approval before any rerun.

## Two-minute explanation

Pipeline errors rarely state the real cause. "Server failed to authenticate" might be an expired secret; a Gold pipeline failure might really be a Silver job that ran out of memory because of data skew. The agent works like an on-call engineer. It reads the run, decides which tools to call (connection status, run history, schemas, Spark metrics, landing files), searches runbooks and past incidents, and submits a report with category, root cause, evidence, fix steps and a recommended action. If it recommends a rerun, the graph pauses until a person approves. I evaluated it on 12 incidents, each with a different root cause, measuring category accuracy, action accuracy, tool recall and an LLM-judged root-cause score.

## Why it fits my background

I worked on ADF pipelines and Databricks transformations as a data engineer, so I know these failure types first hand. This is a personal portfolio project built on synthetic data. It was not built at or for Coforge and uses no company data.

## Short answers to likely questions

**Why LangGraph and not a simple chain?**
The number of steps isn't fixed: the agent decides what to check next from what it found. LangGraph gives me a loop with conditional edges, a step budget, state and an interrupt for human approval.

**Why build the graph yourself instead of a prebuilt ReAct agent?**
I needed a step budget, a separate report step for structured output, and an approval node. As explicit nodes, each one is visible and testable.

**How does the agent stop?**
When it stops calling tools, or after a 6-round budget. A separate report step then turns the investigation transcript into a validated JSON report.

**How do you prevent harmful actions?**
All seven tools are read-only. The only write action, a rerun, sits behind `interrupt()` and runs only after a human approves.

**How do you get structured output?**
A report step sends the investigation transcript to the LLM and asks for JSON, validated by Pydantic with lenient coercion and one retry. At first I used a "submit report" tool, but evaluation showed the model rarely called it and forced calls failed with 400 errors, so I changed the design.

**Why hybrid search?**
BM25 matches exact error codes; embeddings match paraphrases. Reciprocal Rank Fusion combines the two rankings without tuning weights.

**How did you evaluate an agent?**
12 known incidents, scored on category, action, whether it used the right evidence tools, and an LLM judge from a different model family for the root cause. It runs in GitHub Actions.

**How do you test without calling an LLM?**
A scripted chat model returns pre-written tool calls and replies, so tests cover routing, the step budget, report retries, provider errors, tool errors and approve/reject paths offline.

**Why Groq and gpt-oss-120b?**
It's a free tier with good tool calling and fast inference on an open-weight model. Tool outputs are kept compact so a full investigation fits within free-tier token limits.

**What did evaluation teach you?**
Run 1 showed category mix-ups where definitions overlapped, so I added explicit category rules. Run 2 exposed provider errors on the final tool call, so I split the agent into an investigate phase and a report phase. Each change was measured on the same 12 incidents.

**What would you change for production?**
Real APIs behind the tools (ADF REST, Log Analytics, Databricks Jobs), a persistent checkpointer, alert-triggered runs, posting to Teams, and access control on approvals.

## Lakehouse integration (real run logs)

**How is this connected to your lakehouse project?**
I added structured run logs to my PySpark lakehouse, then ran the real pipeline on five broken inputs. The agent diagnoses those real runs with lakehouse-specific tools and proposes the right `--start-from` stage for the rerun.

**How do you know the agent isn't just told the answer?**
The scenario runner writes which failure went into which run to a separate file that is never copied into the agent repo. The agent only sees the run log, source file checks and layer outputs.

**What did you learn from the failure experiments?**
Bronze wrapped every error in a generic `PipelineExecutionError`, so I log the first real `Caused by` as the root error. I also found a gap: if a file isn't re-delivered, the pipeline succeeds on yesterday's data.

**How do you add another platform?**
The graph is shared; each platform supplies its tools, system prompt and rerun action in `environments.py`.
