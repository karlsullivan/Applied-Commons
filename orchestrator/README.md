# Applied Commons Orchestrator

The Applied Commons orchestrator is the persistent control plane for autonomous
and assisted engineering research.

It manages:

- projects
- research questions
- evidence
- findings
- jobs
- worker dispatch
- retries and execution leases

The orchestrator is deterministic infrastructure. Language models and compute
workers propose or execute work through controlled interfaces rather than
owning project state directly.

## Architecture

PostgreSQL is the authoritative state store.

FastAPI provides controlled access to project and job state.

Workers claim compatible jobs atomically and return results to the control
plane.

The initial deployment supports:

- NUC-based lightweight workers
- future local Qwen orchestration
- future NVIDIA Spark heavy-compute workers
