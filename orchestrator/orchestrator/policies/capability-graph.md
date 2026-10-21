# Applied Commons — Civilisation Knowledge System

Maintainer's specification, received 2026-10-21. Kept verbatim as the
reference for the Civilisation Capability Graph; project-selection.md
remains authoritative and is extended from this document.

## Objective

Extend the **existing Applied Commons architecture**, rather than replacing its current project-selection method.

Applied Commons currently organises human needs using the modified **Maslow-style hierarchy**. This remains the primary organising framework.

The required hierarchy is:

```text
MASLOW LAYER
    ↓
CATEGORY WITHIN LAYER
    ↓
NEED / OUTCOME
    ↓
CAPABILITY REQUIRED TO SATISFY NEED
    ↓
IMPLEMENTATION PATHWAYS
    ↓
EVIDENCE / MATURITY
    ↓
GAPS
    ↓
POTENTIAL APPLIED COMMONS PROJECTS
```

The new Civilisation Capability Graph must therefore **extend the current Maslow/project-selection model**, not replace it.

The long-term goal is to build and maintain a machine-readable map of the capabilities required to sustain, improve and, if necessary, rebuild a technologically capable, resilient and broadly egalitarian society.

Applied Commons should remain useful under normal conditions first.

Civilisation recovery is the extreme resilience case.

---

# 1. Preserve the existing Maslow model

Do not replace the current Maslow hierarchy, project-selection logic or existing project records.

The existing conceptual structure remains:

```text
Layer
    ↓
Category
    ↓
What the category covers
```

The new system adds further depth:

```text
Layer
    ↓
Category
    ↓
Need / desired outcome
    ↓
Capabilities
    ↓
Implementations
    ↓
Evidence
    ↓
Gap analysis
    ↓
Projects
```

Existing project selection should continue to function during migration.

Do not require existing records to be rewritten before the new capability system can operate.

Use backward-compatible schema extensions or migrations.

---

# 2. Maslow layers remain the top-level priority mechanism

The basic Applied Commons principle remains:

> Higher-order outcomes cannot reliably compensate for unmet lower-order needs.

Therefore AC should continue to prioritise fundamental human needs before higher-order improvements, subject to urgency, leverage, feasibility and other existing project-selection factors.

The exact layer taxonomy already present in the repository is authoritative.

Do **not** create a second competing hierarchy in code.

The civilisation capability system must reference existing layer/category identifiers wherever possible.

Conceptually, this includes areas corresponding to:

```text
fundamental survival
→ safety and stability
→ social/community capability
→ participation / agency / productive capability
→ learning / knowledge / development
→ higher human flourishing
```

Use the exact existing AC taxonomy rather than hard-coding the illustrative wording above.

---

# 3. Needs and capabilities are different things

This distinction is important.

A Maslow/category node represents **why something matters to humans**.

A capability represents **what society must be able to do to achieve it**.

For example:

```text
MASLOW LAYER:
Fundamental needs

CATEGORY:
Water

NEED:
Reliable access to safe drinking water

CAPABILITIES:
    water acquisition
    pumping
    filtration
    disinfection
    water-quality testing
    storage
    distribution
```

And:

```text
MASLOW LAYER:
Fundamental needs

CATEGORY:
Food

NEED:
Reliable nutritious food supply

CAPABILITIES:
    soil management
    seed propagation
    irrigation
    cultivation
    harvesting
    food preservation
    nutrient management
    agricultural machinery
```

The same capability may support multiple needs.

Do not duplicate it.

Example:

```text
electricity generation
    ├── supports water
    ├── supports health
    ├── supports food
    ├── supports communications
    └── supports manufacturing
```

This is where the Capability Graph complements the Maslow tree.

---

# 4. Architecture

Maintain the existing separation of responsibilities.

### PostgreSQL / Civilisation Capability Graph

Authoritative structured machine-readable knowledge.

Contains:

- Maslow layer/category relationships;
- needs;
- capabilities;
- capability dependencies;
- implementations;
- evidence;
- maturity;
- provenance;
- uncertainty;
- assessments;
- gaps.

### Git

Reproducible artefacts:

- AC code;
- schemas;
- configuration;
- CAD;
- software;
- scripts;
- machine-readable specifications;
- build instructions.

### Notion

Continue using Notion as the human programme-management/control interface.

Use for:

- priorities;
- decisions;
- reviews;
- programme state;
- research oversight;
- executive visibility.

Do not turn Notion into the capability database.

### Applied Commons MediaWiki

Public, collaborative, human-readable expression of AC knowledge.

Eventually it should be:

- self-hostable;
- exportable;
- mirrorable;
- usable offline;
- readable without AI;
- reconstructable independently of AC infrastructure.

Core principle:

> **Applied Commons must never become necessary to use Applied Commons knowledge.**

---

# 5. Civilisation Capability Graph

Implement capabilities below the current Maslow hierarchy.

A capability represents:

> Something a society/community/person must be capable of doing.

It is not a machine.

Example:

```text
CAPABILITY:
precision turning

IMPLEMENTATIONS:
    ├── Gingery-style cast lathe
    ├── welded steel lathe
    ├── restored industrial lathe
    ├── commercial manual lathe
    └── CNC turning centre
```

Minimum capability schema should include:

```yaml
capability_id:
name:
description:

maslow_links:
  - layer_id:
    category_id:
    need_id:
    relevance:

purpose:

requires:
  capabilities:
  materials:
  tools:
  skills:
  energy:
  infrastructure:
  knowledge:

produces:
enables:

implementations:

bootstrap_level:
external_dependencies:
self_reproduction_fraction:

resilience:
  grid_dependency:
  electronics_dependency:
  specialist_dependency:
  supply_chain_dependency:

failure_modes:

evidence:
assessments:
confidence:
uncertainties:
```

A capability may link to several Maslow needs/categories.

---

# 6. Need records

Where useful, introduce a formal `need` layer between category and capability.

Example:

```yaml
need_id: safe_drinking_water

layer:
category:

desired_state:
minimum_threshold:
baseline_threshold:
improved_threshold:

capabilities_required:
```

This should remain compatible with the existing concepts AC already uses such as:

```text
need
baseline
improvement
```

Do not lose those concepts.

They are central to project selection.

---

# 7. Preserve Need → Baseline → Improvement

The existing AC distinction between:

```text
NEED
BASELINE
IMPROVEMENT
```

must remain first-class.

For each need, AC should eventually be capable of representing:

### Need

What must be satisfied at all.

Example:

```text
People require safe water.
```

### Baseline

Minimum acceptable implementation/capability level.

Example:

```text
Reliable microbiologically safe drinking water.
```

### Improvement

Ways of achieving better outcomes beyond baseline.

Example:

```text
lower energy consumption
lower labour requirement
greater resilience
lower cost
better water quality
greater local manufacturability
```

Project selection must continue distinguishing:

> closing an unmet basic need

from:

> improving an already adequate solution.

A lower-layer unmet need should normally carry greater priority than marginal optimisation of a higher-order one.

---

# 8. Bootstrap levels

Add bootstrap levels as a property of implementations/capabilities.

They are **not Maslow levels**.

Do not confuse these taxonomies.

```text
B0 — human labour + natural/local materials
B1 — basic hand tools / forge / simple workshop
B2 — casting + basic machine tools
B3 — general-purpose machine shop
B4 — precision workshop / motors / hydraulics / instrumentation
B5 — industrial production systems
B6 — advanced industry / electronics / optics / sophisticated materials
B7 — globally interdependent high technology
```

Maslow answers:

> **Why do we need this?**

Bootstrap level answers:

> **What productive capability is required to make it?**

These are orthogonal dimensions.

---

# 9. Evidence maturity

Add a separate implementation evidence scale.

Again, this must not replace Maslow or bootstrap levels.

```text
E0 — Concept
E1 — Prototype
E2 — Independently replicated
E3 — Field deployed
E4 — Established engineering
E5 — Industrial commodity
```

Therefore an implementation could be represented as:

```yaml
implementation: gingery_style_lathe

maslow_relevance:
  - productive_capability
  - infrastructure_support

bootstrap_level: B2

evidence_maturity: E2-E3
```

These three axes answer different questions:

```text
MASLOW → human importance

BOOTSTRAP → difficulty of producing capability

EVIDENCE → confidence that implementation actually works
```

Do not collapse them.

---

# 10. Source quality and technology maturity are separate

A well-written project can still be an E0 concept.

A poorly scanned trade manual can describe E5 technology.

Store independently:

```yaml
source_quality:
source_authority:
implementation_evidence_maturity:
```

Never assume:

```text
open source = proven

good documentation = validated

newer = better

popular = reliable
```

---

# 11. Validation questions

For every implementation attempt to determine:

1. Has it physically been built?
2. Has it been built more than once?
3. Has someone independent reproduced it?
4. Has it operated under real-world conditions?
5. For how long?
6. Are performance claims measured?
7. Are results independently verified?
8. Are failures documented?
9. Is maintenance experience available?
10. Is there an established conventional alternative?
11. Does this implementation materially improve upon it?
12. What evidence is required to move to the next maturity level?

Unknowns stay unknown.

Do not replace missing evidence with LLM confidence.

---

# 12. Evidence states

All extracted assertions should use:

```text
SUPPORTED
INFERRED
UNKNOWN
CONFLICTED
```

An LLM inference must never silently become fact.

---

# 13. Source architecture

Support generic source types.

Initial families:

```text
mediawiki
    ├── Appropedia
    ├── Open Source Ecology Wiki
    └── Applied Commons Wiki

git
    └── Open Source Ecology repositories

book/manual
    ├── Gingery
    ├── Weygers
    ├── Aspin
    ├── Sparey
    ├── Connelly
    ├── VITA
    ├── Engineering in Emergencies
    └── other curated references

technical collections
    ├── Practical Action
    └── Hesperian

established engineering
    ├── engineering textbooks
    ├── trade manuals
    ├── vocational curricula
    ├── agricultural extension publications
    ├── humanitarian engineering manuals
    ├── machinery handbooks
    ├── water-treatment references
    ├── industrial manuals
    └── appropriate civil/military engineering references
```

Preserve provenance throughout.

---

# 14. Appropedia

Treat Appropedia primarily as a **discovery source**.

It can reveal:

- candidate technologies;
- appropriate-technology approaches;
- low-resource implementations;
- alternative designs;
- interesting projects;
- sources worth investigating.

Do not assume its projects work.

AC should explicitly determine:

```text
concept?
prototype?
replicated?
field deployed?
established?
```

An unverified Appropedia project may enter AC as:

```yaml
role: candidate_implementation
evidence_maturity: E0
validation_status: unverified
```

It should not be treated as a baseline technology.

---

# 15. Open Source Ecology

Treat OSE as both:

```text
ose-wiki
ose-github
```

OSE is valuable particularly for:

- modularity;
- open hardware;
- candidate implementation architecture;
- CAD;
- BOMs;
- build methodology;
- interface thinking;
- product-ecology concepts.

But:

```text
CAD exists ≠ validated machine

repository exists ≠ working technology
```

Assign evidence maturity independently.

---

# 16. Prioritise established engineering

AC should generally begin with:

> **What does humanity already know works?**

Then ask:

> **How can it become more open, reproducible, repairable, modular and locally producible?**

Prioritise mature material such as:

- engineering textbooks;
- trade references;
- machine-shop manuals;
- vocational curricula;
- agricultural extension material;
- established water/sanitation engineering;
- industrial maintenance manuals;
- electrical references;
- machining/metrology material;
- foundry/welding references;
- proven humanitarian engineering.

Do not invent something new merely because AC can.

---

# 17. Practical Action

Treat Practical Action / Practical Answers as a high-priority technical source, particularly for lower Maslow layers and B0-B4 technology.

Relevant areas include:

- water;
- sanitation;
- agriculture;
- food;
- energy;
- workshops;
- pumps;
- shelter;
- construction;
- disaster resilience;
- small-scale production;
- community infrastructure.

Map every item back to the appropriate existing Maslow layers/categories.

---

# 18. Hesperian

Use Hesperian as a high-priority source for health/community capability.

Examples:

- *Where There Is No Doctor*
- *Where There Is No Dentist*
- *A Book for Midwives*

These predominantly support lower Maslow layers.

Medical material requires strong provenance controls and conservative inference.

---

# 19. Books/manual ingestion

Support a generic:

```text
source_type: book_manual
```

Store bibliographic details and page/section references.

Do not redistribute copyrighted material unless permitted.

Extract structured factual/capability relationships where appropriate.

---

# 20. Gingery

Include David J. Gingery's *Build Your Own Metal Working Shop From Scrap* as a Tier-1 bootstrap reference.

Model its approximate progression:

```text
basic tools + scrap + fuel
        ↓
foundry
        ↓
casting
        ↓
lathe
        ↓
shaper
        ↓
milling
        ↓
drilling
        ↓
dividing/indexing
        ↓
more capable workshop
```

The significant information is not the particular machine designs alone.

Extract:

- dependency order;
- materials;
- tools;
- skills;
- processes;
- productive capability gained;
- downstream capabilities enabled.

Compare against modern alternatives.

---

# 21. Other bootstrap references

Include curated references such as:

### Weygers

Forge, scrap utilisation, hand-tool production, heat treatment and repair.

### Aspin

Small-scale foundry work.

### Sparey

Capability obtainable from a small general-purpose lathe.

### Connelly

Restoration/reconditioning of existing precision machinery.

### VITA

Low-resource village technologies.

### Equipment for Rural Workshops

Progressive workshop capability.

### Tools for Agriculture

Hand → animal → mechanised agricultural production.

### Engineering in Emergencies

Infrastructure in degraded/emergency environments.

---

# 22. Civilisation-overview books

Use material such as:

- *The Knowledge*
- *How to Invent Everything*

primarily to identify missing branches.

Do not treat them as detailed engineering authorities.

---

# 23. Capability extraction

For relevant source material:

1. identify existing Maslow layer/category links;
2. identify need/outcome;
3. identify capability;
4. distinguish capability from implementation;
5. extract dependencies;
6. extract materials;
7. extract tools;
8. extract skills;
9. extract energy;
10. extract infrastructure;
11. identify outputs;
12. identify capabilities enabled;
13. identify failure modes;
14. assign bootstrap range;
15. assign evidence maturity;
16. identify external dependencies;
17. attach provenance;
18. assign confidence;
19. identify contradiction;
20. create candidate graph update.

Do not automatically promote candidates into authoritative knowledge initially.

---

# 24. Multiple implementation pathways

Every major capability should preserve credible alternative pathways.

Example:

```text
PRECISION TURNING

├── purchase existing machine
├── salvage intact machine
├── restore degraded machine
├── fabricate modern welded machine
├── Gingery-style bootstrap
└── CNC/automated implementation
```

Possible comparison dimensions:

```text
bootstrap difficulty
capital
energy
materials
skills
time
productivity
precision
repairability
external dependencies
supply-chain dependency
self-reproduction
evidence maturity
field history
```

---

# 25. Proven baseline → open adaptation

Where a mature solution exists, default development path should generally be:

```text
proven technology
        ↓
understand function
        ↓
identify closed/fragile dependencies
        ↓
open documentation
        ↓
modularise
        ↓
enable substitution
        ↓
improve local manufacturability
        ↓
validate
        ↓
independent replication
```

Novel invention should normally follow failure of easier routes.

---

# 26. AC as validator

AC should be capable of selecting projects whose purpose is to move technology up the evidence ladder.

For example:

```text
E0
↓ prototype
E1
↓ independent replication
E2
↓ field operation
E3
```

Validation is a legitimate project outcome.

---

# 27. Project-selection compatibility

The existing project-selection methodology remains authoritative.

The capability graph provides additional inputs to it.

AC project candidates may now be created from:

```text
Maslow layer/category
        ↓
need
        ↓
baseline deficiency
        ↓
capability deficiency
        ↓
implementation/evidence gap
        ↓
candidate project
```

Or:

```text
Maslow layer/category
        ↓
need already at baseline
        ↓
possible improvement
        ↓
evaluate marginal human benefit
        ↓
candidate project
```

Do not allow highly interesting technical projects to bypass lower-level need prioritisation merely because they are technically attractive.

---

# 28. Project types

Classify potential technical work where useful as:

```text
DOCUMENT
Mature technology exists but knowledge is inaccessible.

OPEN
Technology works but key implementation is closed/proprietary.

REPLICATE
Open implementation exists but independent replication is missing.

VALIDATE
Prototype exists but performance evidence is poor.

IMPROVE
Validated design exists with a meaningful weakness.

INVENT
No adequate solution exists.
```

Prefer, where practicable:

```text
DOCUMENT
→ OPEN
→ REPLICATE
→ VALIDATE
→ IMPROVE
→ INVENT
```

rather than defaulting to invention.

---

# 29. MediaWiki publishing

Generate human-readable pages from approved structured records.

Wiki navigation should mirror the existing Maslow structure as one important entry route.

For example:

```text
Fundamental Needs
    ↓
Water
    ↓
Safe Drinking Water
    ↓
Capabilities
        ├── acquisition
        ├── pumping
        ├── treatment
        ├── testing
        └── distribution
```

But also allow navigation by capability:

```text
Pumping
    ↓
Supports:
    ├── drinking water
    ├── agriculture
    ├── sanitation
    └── industrial processes
```

This permits both:

**human-need navigation**

and:

**technical dependency navigation.**

---

# 30. Bidirectional wiki

Human edits to the AC Wiki must be detected and evaluated.

```text
human wiki edit
→ revision
→ AC detects
→ diff
→ classify
→ extract knowledge change
→ reconcile with graph
→ candidate / conflict / acceptance
```

Wiki edits must not directly overwrite authoritative structured knowledge.

---

# 31. Wiki edit classes

Support:

```text
EDITORIAL
KNOWLEDGE ADDITION
EVIDENCE ADDITION
KNOWLEDGE CONFLICT
```

Evidence additions may justify changes in evidence maturity after verification.

---

# 32. Prevent feedback loops

AC-created wiki revisions must include provenance such as:

```yaml
origin: applied-commons
agent_run:
graph_revision:
generated_from:
```

Human revisions:

```yaml
origin: external-human
```

Do not re-ingest AC publication as independent evidence.

---

# 33. Preserve disagreement

Allow multiple assessments for:

- bootstrap level;
- effectiveness;
- resource requirements;
- maturity;
- evidence;
- appropriate context.

Do not manufacture consensus.

---

# 34. Licensing

Track:

```text
license_original
license_verified
attribution
source_url
source_revision
derivative_status
redistribution_allowed
```

Unknown licence:

```text
license_verified = false
redistribution_allowed = false
```

---

# 35. Offline preservation

Design toward:

```text
Applied Commons Offline

├── human-readable wiki
├── Maslow/need taxonomy
├── capability graph
├── approved manuals
├── software
├── CAD
├── source metadata
├── curricula
└── local search
```

AI may enhance the package but must not be required to use it.

---

# 36. Storage

External source material remains inside the Applied Commons tenant.

Example:

```text
/srv/orchestrator/tenants/applied-commons/data/sources/
```

Maintain strict PlantScope isolation.

---

# 37. First acceptance problem

Run:

> Using the existing Applied Commons Maslow hierarchy and project-selection model, map the human needs that depend on a general-purpose metalworking workshop. Then use established engineering references, Gingery/Weygers/Aspin/Sparey/Connelly, Practical Action, Appropedia and OSE to identify credible pathways for establishing that capability.

The result must show:

```text
Maslow layer
→ category
→ need
→ capability
→ dependencies
→ implementation pathways
→ evidence maturity
→ gaps
→ potential AC project
```

At minimum compare:

```text
purchase
salvage
recondition
modern fabrication
Gingery bootstrap
experimental open-source designs
```

The system must not treat experimental designs as equivalent to established approaches without evidence.

---

# 38. Second acceptance problem

Run:

> Using the existing Maslow hierarchy, analyse the requirement for reliable potable water and determine the minimum capabilities required to satisfy baseline human need at B1-B2 productive capacity.

Compare implementation pathways for:

- acquisition;
- pumping;
- treatment;
- disinfection;
- storage;
- testing;
- distribution.

Then identify:

```text
BASELINE deficiencies
and
IMPROVEMENT opportunities
```

separately.

This is important.

AC should be able to distinguish:

> “people do not have safe water”

from:

> “safe water exists, but the pump could use 10% less energy.”

These should not receive equivalent priority.

---

# 39. Strategic project-selection loop

The target reasoning loop becomes:

```text
MASLOW LAYER
       ↓
CATEGORY
       ↓
NEED
       ↓
Is baseline satisfied?
   ↙             ↘
 NO               YES
 ↓                 ↓
critical gap     improvement opportunity
 ↓                 ↓
CAPABILITIES REQUIRED
       ↓
CURRENT IMPLEMENTATIONS
       ↓
EVIDENCE / MATURITY
       ↓
FRAGILITY / OPENNESS / ACCESSIBILITY
       ↓
PROJECT CANDIDATE
       ↓
existing AC scoring / prioritisation
```

This extends the current model rather than superseding it.

---

# 40. Desired end state

Applied Commons should ultimately maintain two tightly linked maps.

### Human-needs map

```text
Maslow
→ categories
→ needs
→ baseline
→ improvement
```

### Civilisation-capability map

```text
capabilities
→ dependencies
→ implementations
→ evidence
→ productive prerequisites
```

They intersect:

```text
             HUMAN NEEDS
                 ↓
        What should matter?
                 ↓
          CAPABILITY GRAPH
                 ↓
        What must we be able
             to do?
                 ↓
          IMPLEMENTATIONS
                 ↓
         What actually works?
                 ↓
             EVIDENCE
                 ↓
         What should AC do?
                 ↓
       PROJECT SELECTION
```

This relationship is fundamental.

---

# 41. Engineering principles

1. **Maslow hierarchy remains the top-level human-needs model.**
2. **Do not replace the existing project-selection method.**
3. **Need → Baseline → Improvement remains first-class.**
4. **Capability is not the same as need.**
5. **Capability is not the same as implementation.**
6. **Bootstrap level is not Maslow level.**
7. **Evidence maturity is independent of both.**
8. **Proven engineering before speculative design.**
9. **Multiple pathways before prescribed solutions.**
10. **Evidence before inference.**
11. **Concept, prototype and proven technology must never be conflated.**
12. **Preserve sources before transformation.**
13. **Knowledge must remain human-readable without AI.**
14. **Offline survivability is first-class.**
15. **Preserve uncertainty and disagreement.**
16. **Validation is legitimate Applied Commons work.**
17. **Do not reinvent mature technology without a reason.**
18. **Maintain strict AC/PlantScope tenant isolation.**
19. **Everything designed for catastrophic resilience should also have useful normal-world applications.**

---

# 42. Strategic purpose

Applied Commons should ultimately answer two complementary questions.

### Human question

> **What does a person or community need in order to live, develop, exercise agency and flourish?**

Answered principally through the Maslow hierarchy.

### Engineering question

> **What capabilities, knowledge and productive systems are required to satisfy those needs reliably?**

Answered through the Civilisation Capability Graph.

The resulting system should support both:

### Improving society today

by making productive capability more:

- accessible;
- open;
- repairable;
- modular;
- affordable;
- reproducible;
- independently validated.

and:

### Civilisation recovery

by preserving understandable pathways from:

```text
basic survival
→ stable community
→ workshops
→ agriculture
→ infrastructure
→ industry
→ medicine
→ communications
→ science
→ advanced technology
→ human flourishing
```

The purpose of the Capability Graph is therefore **not to turn Applied Commons into a technology catalogue**.

Its purpose is to give the existing Maslow-driven Applied Commons system a rigorous understanding of:

> **what must actually exist in the physical world for human needs to be satisfied.**
