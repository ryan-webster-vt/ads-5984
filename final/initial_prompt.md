I am starting my ADS 5984 Agentic AI final project from scratch. The project is an NCAA Division I Men's Basketball Upset Condition Profiler using a multi-agent architecture.

The only existing files are three reference documents:

- `final_project_reqs_and_arch.qmd` — My project requirements, proposed architecture, agent responsibilities, design decisions, and UML sequence diagrams.
- `final_project_specifications.md` — The instructor's final-project specifications, deliverables, and grading rubric.
- `10 Principles for Reliable Multi-Agent... .pdf` - Suggestions for multi-agent architecture. Use when necessary.

**Your first task is to thoroughly read and understand both documents before writing any implementation code.**

### Step 1: Analyze the reference documents

1. Identify all functional and nonfunctional requirements.
2. Identify every agent, orchestrator, external API, MCP tool, database, embedding model, and other architectural component.
3. Map the proposed architecture to the instructor's grading rubric.
4. Identify dependencies between components and any architectural ambiguities, missing specifications, or potential implementation challenges.
5. Preserve my proposed architecture unless there is a compelling technical reason to suggest a change.

### Step 2: Develop an implementation plan

Create an `IMPLEMENTATION_PLAN.md` that includes:

1. A proposed Python project directory structure.
2. The responsibilities of each Python module.
3. Required dependencies and environment variables.
4. Shared Pydantic schemas and data contracts between agents.
5. A phased implementation roadmap organized by dependencies.
6. Specific deliverables and acceptance criteria for each phase.
7. A testing strategy, including unit, integration, and end-to-end tests.
8. A traceability matrix mapping project requirements to implementation components and tests.

Prioritize building a minimal working end-to-end multi-agent system before implementing expensive historical research, embedding, and statistical validation pipelines.

### Step 3: Initialize the project

After creating the implementation plan:

1. Scaffold the proposed repository structure.
2. Set up `pyproject.toml`, `.gitignore`, `.env.example`, and `README.md`.
3. Establish basic logging, configuration management, and testing infrastructure.
4. Create module directories and necessary `__init__.py` files.
5. Keep the project modular, maintainable, and easy to extend.

### Important constraints

- Do not implement any agents, orchestration logic, API integrations, or Streamlit functionality yet.
- Do not introduce unnecessary frameworks or dependencies.
- Do not invent requirements that are absent from the reference documents.
- Document assumptions and unresolved architectural decisions.
- Prefer deterministic Python code for tasks that do not require LLM reasoning.
- Ensure that all future implementation decisions remain consistent with the reference documents.

### Expected output

When finished, provide:

1. A summary of your understanding of the project.
2. The proposed architecture and directory structure.
3. The implementation phases and their dependencies.
4. Any critical questions or architectural concerns.
5. A summary of the files created.

**Stop after planning and scaffolding. Do not begin implementing Phase 1 until I explicitly approve the implementation plan.**