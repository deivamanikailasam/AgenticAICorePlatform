---
name: research-grounding
labels: [pass, fail]
---
# Research report grounding (research-report, Step 34)

You are grading a short report an AI team wrote to answer a question from documents it retrieved.
The documents and the question are in the task. Grade the report text only; whether the computed
percentage is right is checked separately by code.

**pass** when all of these hold:
1. Every figure in the report appears in, or is computed from, the retrieved documents.
2. It cites the documents it uses (by title or id).
3. It answers the question asked (the change between the two years), not a different one.
4. It states no fact that the documents don't support, and hedges where they are silent.
5. It is short and plain: a reader gets the answer in the first two sentences.

**fail** when any of these is missing, or the report is empty, vague, or contradicts the
documents.

Examples
- pass: "Visitors in Hobart rose from 41,200 in 2023 to 47,500 in 2024, an increase of 15.3%
  (Hobart visitors report 2023; Hobart visitors report 2024)."
- fail: "Visitors grew strongly thanks to tourism campaigns." (no figures, an unsupported cause)
- fail: "Visitors rose 15.3% (source: national statistics)." (cites a source that wasn't retrieved)
