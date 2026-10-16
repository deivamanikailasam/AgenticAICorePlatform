# Compliance sources

Where the framework references in the [control matrix](control-matrix.md) come from, as checked
on **16 October 2026**. Frameworks and laws change; re-check before relying on any of this. The
mapping supports, and does not replace, legal and compliance review.

| Framework | What the matrix cites | Status and caveats (16 Oct 2026) |
|---|---|---|
| **NIST AI Risk Management Framework 1.0** | The four functions: GOVERN, MAP, MEASURE, MANAGE | Cited at function level only. Subcategory numbers are deliberately not given; map them in a compliance review |
| **ISO/IEC 42001:2023** | Annex A control objectives A.2 (policies related to AI), A.3 (internal organisation), A.4 (resources), A.5 (impact assessment), A.6 (AI system life cycle), A.7 (data for AI systems), A.8 (information for interested parties), A.9 (use of AI systems), A.10 (third-party and customer relationships) | Annex A has 38 controls under these nine objectives, per secondary sources. Cited at objective level; individual control numbers and the Statement of Applicability need the standard itself ([certpro](https://certpro.com/hub/iso-42001/controls/iso-42001-controls-list/), [riskprofs](https://riskprofs.com/iso-42001-annex-a-controls-list/)) |
| **EU AI Act**, Regulation (EU) 2024/1689 | High-risk requirements: Art. 9 (risk management), 10 (data governance), 11 (technical documentation), 12 (record-keeping), 13 (transparency), 14 (human oversight), 15 (accuracy, robustness, cybersecurity), 19 (logs), 50 (transparency to people), 72 (post-market monitoring) | **When high-risk obligations start is unsettled.** The original text applies them from 2 August 2026. Secondary sources report a Digital Omnibus deferral to 2 December 2027 for standalone Annex III systems; not confirmed against an official source here. Check EUR-Lex and the Commission's timeline ([Mishcon de Reya on Art. 12](https://www.mishcon.com/eu-uk-ai-navigator/article-12), [SureCloud](https://www.surecloud.com/blog-hub/eu-ai-act-technical-compliance-requirements)). Whether any agent here is "high-risk" under the Act is a classification for legal review, recorded per agent by its risk tier |
| **Australian Privacy Principles** (Privacy Act 1988) | APP 1 (open and transparent management), 3 (collection), 6 (use and disclosure), 10 (quality), 11 (security), 12 (access), 13 (correction) | **New automated-decision transparency duties (APP 1.7–1.9) commence 10 December 2026** under the Privacy and Other Legislation Amendment Act 2024. They apply to entities that use a computer program, with personal information, to make or substantially support decisions that could significantly affect a person. Privacy policies must then describe those decisions. OAIC guidance was expected by September 2026; check its status ([Corrs](https://corrs.com.au/insights/automated-decision-making-preparing-for-the-new-transparency-obligation), [Maddocks](https://www.maddocks.com.au/insights/automated-decision-making-privacy-obligations)) |
| **Australian Government Guidance for AI Adoption** (National AI Centre, October 2025) | The six practices: decide who is accountable; understand impacts and plan accordingly; measure and manage risks; share essential information; test and monitor; maintain human control | It replaces the Voluntary AI Safety Standard (10 guardrails) and is voluntary. Sources give its release as 17 or 21 October 2025 ([White & Case](https://whitecase.com/insight-alert/australia-launches-new-ai-guidance), [Allens](https://www.allens.com.au/insights-news/insights/2025/11/governance-doesnt-stand-still-9-faqs-to-help-understand-the-governments-new-guidance-for-ai-adoption/)) |
| **OWASP Top 10 for Agentic Applications** (December 2025) | ASI01–ASI10, mapped per threat in the [threat model](threat-model.md) | As named in the spec; every category has automated tests (Step 35) |

## What this means for the platform

- **Automated decisions (APP 1.7, from 10 Dec 2026).** A deployment where an agent's output is
  used, or substantially used, for decisions about people must be listed in the deployer's
  privacy policy. The AI-use register (`docs/governance/ai-use-register.md`) and the agent cards
  give the facts; writing the policy is the deployer's job (control C22).
- **EU high-risk use.** If an agent is classified high-risk under the Act, the evidence for
  Art. 12 (C09 audit trail), Art. 14 (C03 approvals, C14 kill switches) and Art. 15 (C01, C02,
  C04, C11, C16) is in the matrix. The classification itself, and conformity assessment, are not
  platform features.
