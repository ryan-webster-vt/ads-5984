# UML Diagrams

Mermaid sources for the class diagram and the three approved sequence diagrams.
Render them with the Mermaid CLI or any Mermaid-compatible viewer:

```bash
npx @mermaid-js/mermaid-cli -i docs/uml/class_diagram.mmd -o docs/reports/class_diagram.pdf
npx @mermaid-js/mermaid-cli -i docs/uml/sequence_research.mmd -o docs/reports/sequence_research.pdf
npx @mermaid-js/mermaid-cli -i docs/uml/sequence_extraction.mmd -o docs/reports/sequence_extraction.pdf
npx @mermaid-js/mermaid-cli -i docs/uml/sequence_monitoring.mmd -o docs/reports/sequence_monitoring.pdf
```

| File | Purpose |
|---|---|
| `class_diagram.mmd` | Agents, contracts, services, integrations, storage, and collaborators |
| `sequence_research.mmd` | Historical research ingestion (orchestrator-worker, parallel workers, validation, quarantine) |
| `sequence_extraction.mmd` | Candidate precondition extraction and statistical acceptance |
| `sequence_monitoring.mmd` | Daily monitoring, parallel Monitor workers, and upset ranking |

The sequence diagrams mirror the approved architecture in
`reference_documents/final_project_reqs_and_arch.qmd`. Keep them in sync with
the implementation and regenerate the submission PDF from these sources in
Phase 9.
