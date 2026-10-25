Applied Commons project selection

Repository location: orchestrator/policies/project-selection.md
Companion policy: Needs taxonomy

This document defines how Applied Commons admits, prioritises, continues and completes engineering projects. Runtime enforcement must be implemented and verified separately; adding this file alone does not change orchestrator behaviour.

1. Purpose and scope

Applied Commons develops reusable engineering software and hardware that makes essential human needs easier to meet, reduces everyday burdens, and gives people greater freedom to pursue relationships, learning, creativity and purpose.

The primary objectives are effectiveness, affordability over the solution's useful life, robustness and confidence supported by evidence. Substantial research compute is justified when it can improve the result or resolve an important uncertainty.

The development focus is the physiological and safety layers in taxonomy.md. Work associated with higher layers may support an admitted project when it has a specific, necessary contribution to that project's outcome. Independent expansion of the mission requires an explicit maintainer decision.

2. Classification

Assess each proposal in this order:

The usual human-need layer.

The category within that layer.

The specific need, intended users and operating setting.

The measurable outcome the project proposes to improve.

Use the definitions in taxonomy.md; do not duplicate the taxonomy here. Assign one primary layer and category based on the direct intended outcome. Record secondary benefits and engineering methods as separate tags. Energy, communications, sensing, fabrication, robotics and software can support many different needs.

Interpret requests semantically. Keyword matches alone must not determine admission or classification. Ambiguous proposals may remain unclassified candidates while their purpose is clarified. A new problem does not need to belong to an existing project.

Explain the link between the work and the need. A remote possibility that a technology might eventually benefit humanity is insufficient for admission. Models may suggest classification changes, but may not expand the mission or rewrite this policy during an investigation.

2a. Enablers

Some work is not a need in itself but something many needs depend on: energy, communications, environment and climate, fabrication, instrumentation and control, skills and others (taxonomy.md, Enablers). Enablers form a track alongside the layers rather than a layer of their own. Each enabler category has its own need brief, framed by the essential needs that depend on it, and its own discovery.

An enabler project is screened on the needs it serves: its assessment lists the physiological and safety need categories it directly makes easier to meet, and the project takes the most basic layer among them for the need-layer score. The other dimensions are scored on those needs. Environment and climate as an enabler (carbon removal, ecosystem restoration, adaptation) is distinct from the Safety category Environmental stability and resource security (exposure and access), and both remain.

Enablers compete for the same slots under the same rubric and thresholds, with a slight attention bump: their pipeline steps queue a little ahead, and admission orders them as if their composite were 0.05 higher. The recorded composite is not changed.

2b. Climates

Needs and solutions differ by climate. Each screening and review records the climate groups a project works in year-round (tropical, arid, temperate, cold, polar), from evidence or sound engineering reasoning. Cold climates (long freezing winters and low winter sun: Canada, Scandinavia, Scotland, the northern US, Alaska) are a target in their own right: need briefs cover cold-climate settings and say where requirements differ there, and each discovery includes at least two candidates that work year-round in cold climates, where such projects exist. Climate does not change the rubric; it records where a result applies.

The maintainer's land in Estonia is a proving ground for physical trials. Its seasons stand in for several climate groups (summer for temperate conditions, winter for cold ones), so trials are chosen by the season that matches the target climate, not limited to the local climate. Results apply to the conditions tested (SAFETY.md).

3. Admission requirements

Every candidate must address the following six requirements. Distinguish established facts, estimates, assumptions and unknowns. Early feasibility work may investigate missing evidence; development admission requires a sufficiently credible case and a defined route to testing it.

Requirement

Required assessment

Need

Identify the intended users, setting, unmet need, its severity and the evidence that it exists. Explain why solving it would matter to those users.

Baseline

Identify the practical alternatives already available, including existing open designs and the current local approach. Describe their performance, costs and limitations under comparable conditions. Record relevant sources and versions.

Improvement

Specify a measurable improvement in effectiveness, lifetime cost or robustness. Define minimum requirements for the other dimensions, intended operating conditions and the assumptions that could change the conclusion.

Demonstration

Define how the improvement can be assessed through reproducible software tests, calculations, simulations, experiments or a realistic prototype. Identify required equipment, skills and external inputs.

Burden removed

Assess changes in recurring expenditure, labour, time, attention, uncertainty, risk and dependence. Include any burden transferred to maintainers, carers, communities or other users.

Practical independence

Assess whether intended users can obtain, build, operate, understand, maintain and repair the solution with realistic resources. Consider parts, tools, documentation, skills, connectivity and specialist support.

Improving, combining, simplifying or documenting an existing approach is a valid project. Novelty alone earns no priority. Reuse must respect the repository's existing licensing requirements and the terms attached to source material.

A candidate must state its immediate engineering question, expected deliverable, acceptance criteria and next evidence checkpoint. Search for overlapping work before creating a new project; related questions should normally extend the existing investigation.

4. Prioritisation

Give strong preference to substantial unmet needs in the lower layers. Assess the severity and persistence of the need, who benefits, the credible magnitude of improvement, and the prospect of delivering a usable result. A layer number by itself does not establish project value.

Compare proposals using their admission assessments and supporting evidence. Record a short explanation of why the selected work deserves attention now, including material uncertainty and the opportunity cost of changing direction.

Evaluate affordability using construction or deployment cost, energy, consumables, maintenance, repairs, replacement and human effort over a stated service life. Separate these costs from the research compute used to develop the solution.

Set minimum effectiveness and robustness requirements before comparing cheaper approaches. Lower cost cannot compensate for failure to perform the required function. Robustness includes realistic environmental variation, component variation, failure recovery, repairability and supply availability where relevant.

Numerical scores may assist comparison when their definitions and evidence are explicit. An unexplained model score or self-reported confidence must not decide admission, continuation or completion. Uncertainty that can be meaningfully investigated may justify deeper work.

4a. Rubric v1 (autonomous admission)

Admission is autonomous: the orchestrator admits candidates that meet the rule below without a human approval step (section 9). The rubric is implemented in orchestrator/app/research.py (rubric-v1); this section and that file must change together.

Every candidate receives a candidate-assessment job. The assessment returns a 0 to 1 score for each assessed dimension, a written answer to each of the six section 3 requirements ("unknown" where evidence is missing), a rationale and up to five research questions.

Dimension

Weight

Meaning

Need layer

0.20

Fixed by the candidate's taxonomy layer: physiological 1.0, safety 0.8, belonging 0.4, esteem 0.3, self-actualisation 0.2.

Need severity and reach

0.20

How severe and persistent the need is, and how many people it affects.

Effectiveness

0.15

Credible improvement over the baseline in the intended setting.

Ease

0.15

Ease of use and of implementation for the intended users.

Cost

0.15

Affordability over the service life (section 4); higher means cheaper.

Practicality

0.15

Practical independence: parts, skills, repair and support (section 3).

The evidence score is not weighted in; it scales the result. The composite is the weighted sum multiplied by (0.5 + 0.5 × evidence), so weak evidence at most halves a candidate's score. A promising but under-evidenced candidate stays on the shortlist without being admitted on unsupported claims.

A candidate is admitted when all of the following hold:

The composite is at least 0.55.

The evidence score is at least 0.3.

The need, baseline and improvement requirements are answered, not "unknown". Demonstration, burden removed and practical independence may remain open; the admitted project investigates them.

At least two sources support it, counting source URIs and recorded evidence.

An active project slot is free (section 5).

Eligible candidates are admitted in descending composite order. Each admission is written to the decisions table with the composite, evidence score, rationale, policy revision and the author "applied-commons orchestrator, rubric v1". Admission opens the candidate's research questions.

Results that do not match the job's output format are rejected before anything is written. The scores support the section 4 judgement; the written rationale and requirement answers record why.

Need first. Before candidates are sought for a focus category, a need-brief job establishes the need itself: who lacks it met and where, its severity, current practice and its cost, measurable requirements a good solution must meet, practical constraints, and where existing solutions fall short. Discovery in a category waits for its brief, and discovery and assessment judge candidates against it. Briefs are refreshed every 90 days.

4b. Go/no-go review (review v1)

Admission buys a project its research questions, not a build. Once every open question of an admitted project is settled (answered, or its research failed for good), a project-review job re-scores the project on the rubric with the evidence gathered, states how it meets the brief's requirements, lists what upstream does not document, and recommends build, continue or park.

The orchestrator decides, not the model alone:

Build when the review recommends it, the recomputed composite is at least 0.60 and the evidence score at least 0.4.

Continue when the review asks for up to three specific questions that would settle the decision; they are opened and researched, then the project is reviewed again.

Park otherwise, including a build recommendation the rubric does not support with no questions to resolve it. A project gets at most three reviews; the third decides build or park.

Each review and outcome is recorded (reviews and decisions tables, author "applied-commons orchestrator, review v1"). Parking frees the project's slot.

4c. Build packs

A build decision is the compelling reason to invest substantial compute. The build pack documents everything needed to build one unit from public material, as four independent steps:

Bill of materials: every part with specification, quantity, unit cost, currency, supplier link and locally available alternatives, and the cost basis.

Design files: every git repository (pinned to a commit, with its licence) and design file (CAD, drawings, schematics, PCB, firmware, data) with a direct link and licence.

Assembly: ordered steps with safety precautions, tools, skills and time.

Test: acceptance criteria tied to the brief's requirements, safety checks and known failure modes.

Each step also records the gaps upstream leaves. When all four steps have finished the project is complete and its slot frees up. Build packs document existing designs; they do not validate them (section 6). A completed build pack is not evidence that a build is safe or performs as claimed.

Design files are kept as pinned local copies (ops/archive/archive_designs.py): a repository at its recorded commit and direct file downloads, only when the repository's licence file or the reported licence is a recognised open licence (one that allows changes and commercial use: not a non-commercial or no-derivatives licence, nor an unnamed "Creative Commons" one); anything else stays a link. The archiver fetches only https sources on public addresses. A file the project hosts itself, with no licence of its own, takes the project's open licence; a file a site refuses to servers is copied from its Wayback Machine snapshot.

Designs are captured, not just linked (maintainer decision 2026-10-23). Every design source is also held by a public archive, whatever its licence, since those archives keep material Applied Commons may not republish: repositories by Software Heritage, cited by SWHID (the pinned commit when it is archived), and files and pages by the Internet Archive's Wayback Machine, cited by snapshot. The archiver looks for an existing copy first (a Wayback snapshot counts when under a year old) and asks for a save only when there is none.

4d. Maintainer steering

The maintainer may steer any project: build forces a build pack (admitting a candidate if needed, outside the slot limit), park parks it. A steer is recorded as a decision with author "maintainer (steer)" and acts once; clearing it does not undo what it caused.

Priority (maintainer decision 2026-10-23): the maintainer may make a project a priority, name a project the catalogue lacks (it enters as a candidate and is assessed like a discovered one), or ask for a discovery focused on a topic in a category. A priority project is admitted as soon as it is assessed, outside the slot limit and the admission threshold, with author "maintainer (priority)", and every job for it is taken up before other work. It still goes through research and the go/no-go review, so a priority decides what is worked on first, not what is built. A focused discovery is also taken up first; when asked to, it makes its best-assessed candidate a priority once every candidate it found has been assessed.

One project, several needs (maintainer decision 2026-10-23): a project is catalogued once, under the category it fits best, and listed under every other need it serves. Discovery that finds a known project again (the same name, or the same repository on a code host) lists it under the new category instead of adding it twice. The maintainer merges duplicates already catalogued: the duplicate keeps its records with status merged and is replaced everywhere by the project it was merged into, which takes over its sources, relations and priority; work queued for the duplicate stops.

4e. The modular set (stage 2)

Applied Commons works in four overlapping stages: (1) understand the needs and what exists (sections 2 to 4d); (2) engineer a modular set from it; (3) field trials and verification at the proving ground; (4) continuous improvement, where trial results revise the set and produce Applied Commons' own improved designs. Stage 2 is modelled on Open Source Ecology's Global Village Construction Set, across every need and enabler.

Interface standards: the shared electrical, fluid, mechanical, data, thermal and software interfaces modules connect through. A standards-synthesis job proposes them monthly from everything stage 1 has found, preferring existing, cheap, worldwide standards. The maintainer approves or rejects each one; a decided standard is not changed by later proposals.

Verification: before the maintainer decides, each proposed standard is checked against the established standards for its interface (ISO, IEC, EN and national adoptions such as DIN, AS/NZS, and others such as UL where relevant) by a standard-verification job, again whenever its specification changes. It cites the relevant standards, lists every deviation with its risk and any justification, says whether the interface is safety-critical, gives the controls a deviation needs, and recommends approve, revise or reject. A standard is approved only once its current specification has been verified; rejecting needs no verification. The maintainer may supply a verification instead of waiting for the job, for example one done by an assistant with more capacity (maintainer decision 2026-10-20); it is recorded with who did it, against the specification it checked, and replaces the queued job. Where the same assistant drafted the standard, the verification says so. The aim is to build nothing unsafe without adding needless overhead: adopting an existing standard outright is preferred, and a verification works from catalogue pages and reputable summaries where full texts are paywalled, saying what it could not check.

Revising a proposal (maintainer decision 2026-10-19): when a verification recommends revise, the next monthly standards-synthesis receives its required controls and deviations and returns the standard under its own code with the spec revised to meet them; it is then verified again. The maintainer can also revise a proposed standard directly, or propose one a synthesis missed; the earlier version and its verification are kept in the standard's history. A proposed standard the maintainer wrote or revised, or one whose current specification is verified for approval, is held for the maintainer's decision and no synthesis rewrites it. Approving with a note is not a way to change a standard: modules are specified from its text, so the text is what gets revised.

Keeping standards current: a monthly standards-review checks each approved standard (approved at least a week before) against everything found since: its adoption, counted from the records (the modules that use it and the systems built from them); conflicts, meaning findings that contradict it or show it fits poorly, each with its source; and a recommendation to keep, revise or retire it. A revision is a new proposed standard that supersedes the old one; it is verified and needs the maintainer's approval like any other, and once approved the old standard is marked superseded and module synthesis moves modules onto the new one. Rejecting an approved standard retires it. Change needs a clear reason, because every module built on a standard would have to change. Approved standards are also re-verified every year against current editions of the established standards, so a revised ISO or IEC edition is caught.

Modules: reusable, self-contained hardware or software building blocks. Once at least one standard is approved, a module-synthesis job per need or enabler domain (with at least two screened projects) runs monthly, reusing and extending existing modules rather than duplicating them. Modules connect only through approved standards; a module needing an uncovered interface proposes a standard and waits for it. Module synthesis also sees the standards still waiting for the maintainer, and references one that covers the interface instead of proposing a duplicate, and the rejected ones with their reasons, so they are not proposed again; it proposes standards only for interfaces shared between modules, not for one module's own ports, records or a product's API (maintainer decision 2026-10-20). A module, system or roadmap job reads the standards and modules again when a worker takes it up, not only when it is queued, because a wave can wait days while the maintainer decides standards. Each module is costed at EU prices and at low-income-country prices with the cheapest locally available substitutes. A model may describe a module as a concept or documented; built and tested are set only by trials.

Systems: compositions of modules for five scenarios: a single-family household; a homestead (the same family on a larger plot); a small farm; a community facility such as a clinic or school; and a village. A system-design job per scenario runs monthly after that month's module syntheses have finished. It does the engineering: calculations such as energy, water, thermal and load budgets, kept as code with inputs and results and labelled calculated or simulated; interface checks; costs; failure modes; and the modules still missing.

Roadmap: a roadmap-revision job runs monthly after that month's system designs have finished. It orders standards, modules, systems and trials by dependency and leverage, cheapest and most transferable first, and places each trial at the proving ground in the season matching its target climates (section 2b). The maintainer approves each version; one roadmap is approved at a time. Trials (stage 3) are proposed from approved roadmap steps.

Proving-ground profiles are kept on the host, not in this repository, and reach only roadmap revisions.

4f. Project relations

A project-relations job relates catalogue projects to each other, so that a reader finds what a project depends on or competes with: uses (a house design cut on a CNC router uses the router), enables, alternative, and part-of. It runs monthly and each time another 25 projects have been assessed, over the most promising 200 assessed projects, and adds to the relations found before. Relations link projects in the published wiki; they are not evidence about either project.

4g. Source checks

Every project's sources are checked automatically, without a model (ops/checks/check_sources.py), when first catalogued and every 14 days after: whether its links answer (ok, broken, blocked by the site, unreachable), its licence, and whether its repository is maintained or archived. The licence comes from the GitHub or GitLab API where the project has a repository there (verified); otherwise from what its build pack reported (unverified). Each project is classed open, share-alike, restricted (non-commercial or no-derivatives), none (a repository without a licence, so all rights reserved) or unknown (an unrecognised licence or nothing to check). Modules build only on open and share-alike projects (section 9); a project classed none or unknown may still be catalogued and researched, and its authors can be asked to add a licence.

4h. Progress towards done

A need is done when modules tested in the real world substantially solve it (section 9). Progress is tracked against the requirements in its brief. A need-progress job matches each requirement to the projects, modules and systems that meet it, at one of five levels: none; candidate (a screened project claims it); documented (a project with a complete build pack under an open or share-alike licence meets it); designed (a module or system of the modular set meets it); field-tested (a module tested in the real world meets it). The orchestrator lowers any level the records do not support, so a model cannot overstate progress. A need's status is its weakest requirement's level. Only the maintainer marks a need substantially solved, and only once at least one requirement is field-tested; continuous improvement (stage 4) then closes the remaining gaps. The job runs when what could meet a need changes, at most weekly per need; while one is waiting to run, a change refreshes it rather than queuing another.

4i. Safety review of build packs

Each completed build pack gets a safety-review, and again whenever any of its sections is rewritten: the hazards of building, installing, using and maintaining it (electrical, battery, gas and combustion, pressure, heat and fire, structural, potable water and food contact, chemical, mechanical, biological and medical, radio), each with its severity, the established standards that govern it, the controls needed and whether the build pack already covers it; and a verdict: ok, controls-needed, qualified-person (a step needs a licensed or qualified person, such as mains wiring, gas fitting, pressure vessels, load-bearing structures or medical use) or do-not-build. The verdict is never milder than the hazards listed: an uncovered medium or high hazard needs controls, and a high electrical, gas, pressure, structural or medical hazard needs a qualified person. Nothing is built at the proving ground (stage 3) without a safety review, and its controls become part of the trial plan. A safety review is not a certification.

4j. Monthly catalogue audit and gap analysis

Once a month, once at least ten projects have been screened, a catalogue-audit job reviews the whole catalogue (every need and enabler with its brief's requirements, its progress and its project counts, and the projects, standards and modules) against independent reference lists: the Sphere Handbook minimum standards, the WHO essential medicines and priority medical devices lists, Open Source Ecology's Global Village Construction Set, Dartnell's The Knowledge and the UN basic-needs SDG targets, as well as the briefs themselves. It lists up to 30 gaps, most important first: a need or part of a need nothing meets, a category with too few or too weak candidates, a missing standard or module, or a quality problem. Each gap names the reference that shows it, its severity (high for basic survival or safety needs) and a suggested action. The maintainer decides each gap (maintainer decision 2026-10-20): acting on a discover gap in an existing category queues a candidate-discovery job focused on that gap, alongside the category's regular scans; other actions (a new category, a standard, a module, a review) are recorded for the maintainer to take up; an ignored gap is not raised again unless something changes. The next audit closes the open or acted gaps the catalogue now covers. The audit informs priorities; it does not change the rubric or admit projects by itself. A restart scenario (rebuilding essential services without global supply chains) may be added to the audit once projects are more mature.

4k. Civilisation Capability Graph

The capability graph extends the classification in section 2 below the category; capability-graph.md holds the maintainer's specification, and this section is how it is applied (maintainer decision 2026-10-21). The layers, categories and the selection method in sections 2 to 4j stay as they are, and the graph adds no hierarchy of its own: every record refers to an existing category.

A need record states one need within a category: its desired state, its minimum, baseline and improved thresholds, and the improvements that would matter beyond the baseline. A capability is what a community must be able to do to meet needs, such as pumping, disinfection or precision turning. It is not a machine, and one capability serves every need that depends on it rather than being repeated. Capabilities are linked to the categories and needs they serve (required or supporting) and to the capabilities they require. An implementation is one way of providing a capability: purchase, salvage, recondition, fabricate, bootstrap (as in Gingery's progression from foundry to lathe), an experimental open design, or a practice. A catalogue project or module can be one.

Three axes stay separate. The Maslow layer, from the category, says why it matters. The bootstrap level says what productive capability making it takes: B0 human labour and local materials; B1 hand tools, forge and a simple workshop; B2 casting and basic machine tools; B3 a general-purpose machine shop; B4 a precision workshop with motors, hydraulics and instrumentation; B5 industrial production; B6 advanced industry, electronics and optics; B7 globally interdependent high technology. The evidence maturity says how well it is shown to work: E0 concept, E1 prototype, E2 independently replicated, E3 field deployed, E4 established engineering, E5 industrial commodity. Source quality and authority are recorded separately from the maturity of the technology a source describes: a well-written project can be a concept, and a poor scan of a trade manual can describe established engineering.

Evidence: every claim carries an evidence state, SUPPORTED, INFERRED, UNKNOWN or CONFLICTED. That covers a link, a dependency, an answer to one of the twelve validation questions, and an assessment of bootstrap level, maturity, effectiveness, resources or context. A SUPPORTED claim needs an independent source with a page or section reference; one without is kept as INFERRED. Applied Commons' own publications, its wiki included, are never independent evidence. An implementation's evidence maturity is the level its sourced evidence supports, and E0 without any, whatever its proposer claims; the claim is kept beside it. Claims are added, not overwritten, so disagreement stays visible, and unknowns stay unknown.

Sources keep their provenance and licence: the original licence, whether it was verified, attribution, revision and whether Applied Commons may redistribute it. An unknown licence is unverified and not redistributable. Copyrighted books and manuals are cited by page or section, and only short excerpts are stored.

Approval: everything enters as a candidate, whether from an extraction job, an assistant or the maintainer. A later update never changes an approved or rejected record; it can only add claims about it. A capability is approved only once it is linked to a need it serves, and an implementation only once its capability is approved. Links and dependencies are claims on those records, and the maintainer can reject a wrong one. The approval model (maintainer decision 2026-10-21): an assistant reviews candidates; safety-critical and medical records (health, and reproductive and early-life needs) also get an independent check, named in the approval; and the maintainer approves in batches. The independent check is normally a graph-review job: the AC research worker checks the candidate's claims at its current revision against sources it retrieves itself, lists missed hazards and recommends approve, revise or reject. Its sourced findings are recorded as claims. An approval without another named check uses a review of that revision that recommends approve. A review recommending revise or reject blocks approval unless the maintainer names another check, and a changed candidate is reviewed again.

The graph informs project selection and does not replace it. It separates a baseline deficiency, meaning a required capability with no approved implementation at field-deployed evidence at the bootstrap levels in question, from an improvement opportunity on a need already met. A lower-layer baseline deficiency normally comes before an improvement elsewhere, and a technically attractive project does not bypass that. A project may record its work type (document, open, replicate, validate, improve or invent), the capability it serves and whether it closes a baseline gap or improves an adequate solution. The types are preferred in that order, so mature technology is documented and opened before anything is invented. Moving an implementation up the evidence ladder is legitimate work.

The whole graph exports as one self-describing document (the scales, the taxonomy, the records, their claims and the sources' metadata), so it stays usable without Applied Commons or a model. Phase 1, the data model and its API, is in place. Extraction from sources, integration with selection, the public wiki and the offline package follow in later phases.

4l. Production library

Applied Commons aims to decentralise and reinvent the means of production, starting with a small community farm and workshop connected to regional specialist producers (maintainer instruction 2026-10-25). The production library maps what that takes: equipment, materials and components, process routes, specific designs, shared modules, external dependencies, enterprises and organisation models, grouped into eight local service systems and ten wider industries. Its first content is an outside package, Applied Commons Production Library v0.4.

A package is staged beside the canonical records, never into them. The Maslow layer, category, need and capability hierarchy of section 4k, its evidence states and its B0 to B7 levels stay authoritative; the package's capabilities, systems and industries are proposals, and linking one to a canonical record is a review decision. An import is idempotent and protective: records are keyed by origin and stable ID, an unchanged payload changes nothing, nothing is deleted, a record missing from a later package is flagged, a renamed record is a conflict rather than a silent replacement, and a record a person has reviewed or verified is never overwritten by a seed. Every refused or changed record opens a review task. Verified needs sourced evidence.

Meaning is kept as the package gives it. Requirement groups (all of, one of) are authoritative and stored intact; graph edges are navigation only and never an AND list. Unknown stays unknown, never zero and never a failure. A located source is not a validated design: a design counts as reviewed only once its exact revision, editable assets, complete bill of materials, licence of each asset and build and test evidence are captured. A shared module's interoperability, repairability and independent reproduction are unknown until a verified review supplies evidence; they are never inferred from equipment names or graph connections. Equipment is grouped by community scope (core candidates, optional enterprises, regional specialists, external supplies), and the full catalogue is not an on-site requirement. An enterprise's revenue is potential until evidenced, and revenue, profitability, household savings and community-service value are kept apart, as are technical, market, economic and realised evidence. A bootstrap cycle stays visible until a named purchased or outsourced starting point resolves it.

The import proposes links to catalogue projects (same source, or same site and name), to capabilities (shared words and the same domain) and to categories (an editorial domain map), and opens research tasks: design audits (the package's audit backlog first), licence reviews, reconciliation of each proposed capability, interface specifications for shared modules, supply data for external dependencies, market and cost evidence for enterprises, discovery leads and bootstrap cycles. Existing open-source projects in the catalogue speed this up: a confirmed link brings their source checks, pinned archives and build packs to the design. The public site shows the library beside the needs, opening on the local service systems.

5. Focus and project limits

The portfolio runs projects in parallel, limited by available compute (section 9):

Up to MAX_ACTIVE_PROJECTS active projects at once (default 8). Each is a feasibility investigation until it reaches a development checkpoint.

The number of concurrent research steps is limited by the executor, not by the project count. Apollo dispatches Applied Commons work only while the Spark reports itself healthy with spare capacity, and only in a bounded number of parallel steps.

These limits apply across Applied Commons as a whole. Categories do not receive independent project allowances. Subprojects, supporting tools and follow-up questions retain their parent project's scope and resource accounting. A distinct independent outcome requires a separate admission decision.

Keep a finite, deduplicated shortlist of candidates. Discovery has an explicit scope: one candidate-discovery step per physiological or safety category in taxonomy.md every 14 days, each returning at most 20 named open-source projects or approaches with sources. Discovery remains subordinate to useful progress on admitted work. Do not generate or investigate projects merely to populate categories or occupy available compute.

When every slot is occupied, new candidates wait on the shortlist until a project completes or is parked. Record the reason for a switch and preserve the existing work. Temporary PlantScope interruptions retain the current project's place. Reopening parked work requires new evidence, changed constraints, a resolved blocker or a recorded maintainer decision.

The orchestrator enforces project limits independently of model recommendations. Changes to those limits require a maintainer decision recorded in version control.

6. Evidence and confidence

Confidence must be supported by traceable evidence appropriate to the claim:

Link material claims to retrieved sources, calculations or test results. Preserve relevant source versions, repository commits and licensing information.

Record assumptions, units, operating conditions, input data and material uncertainty. Retain contradictory findings and failed tests.

Assess performance against the baseline under comparable conditions. Investigate failure cases and sensitivities that could invalidate the proposed improvement.

Preserve reproducible code, designs, calculations and test instructions alongside the findings.

Distinguish proposed, calculated, simulated, experimentally tested and independently reproduced results. A simulated result must not be presented as demonstrated physical performance.

Model agreement and repeated generation may help identify questions, but do not constitute independent validation. The evidence required depends on the intended claim and use. When a claim needs physical testing, record that requirement and the current limitation explicitly.

7. Compute allocation and continuation

PlantScope has first priority on the Spark. Applied Commons uses available capacity and must yield the compute and memory PlantScope requires. Preserve completed steps, evidence and the next intended action when interrupted. Resume from saved progress when capacity returns; an interrupted step may need to be rerun.

Within Applied Commons, jobs run in priority order, and a waiting job gains 0.01 priority per day it waits, up to 0.15 (maintainer decision 2026-10-19), so lower-priority work such as need progress, discovery and literature collection is not starved by a steady flow of higher-priority jobs, while a fresh verification or module job still goes first.

Resource allowances are planning and review points. A full day or longer of available compute is acceptable when supported by a concrete investigation and a credible path to useful evidence or a better engineering result. Elapsed time or token consumption alone is not a reason to abandon a worthwhile project.

At each substantive checkpoint, record:

What was learned or produced, including useful negative results.

Which important uncertainty or requirement remains unresolved.

The next action and how its result could change the engineering decision.

The resources and external inputs that action requires.

The orchestrator may automatically extend work that remains within the admitted scope and has a justified next action. Routine continuation does not require repeated human approval. A failed experiment can justify further work when it eliminates an option or informs a better approach.

Decision

Basis

Continue

The next action has a credible prospect of resolving an important uncertainty or improving the result.

Change approach

Evidence weakens the current approach and supports a different route within the project's scope.

Pause

Progress requires unavailable compute, physical testing, equipment, information or another specific dependency. Record the resume condition.

Park

Evidence undermines feasibility or usefulness, no credible next action is available, or a justified portfolio decision displaces the project. Preserve the findings and reopening condition.

Complete

The admitted deliverable and its acceptance criteria have been met, with supporting evidence and limitations recorded.

Repeated actions that add no evidence must trigger reassessment. Technical timeouts and retry limits should stop a stuck step while preserving progress; they do not by themselves determine the merit of the project. The system may remain idle when no useful eligible action is available.

8. Responsibilities and decision records

The small NUC model may interpret requests, identify related work and prepare preliminary assessments. The Spark model may investigate promising or ambiguous candidates, critique plans and propose follow-up work. Material decisions require recorded reasons and evidence; neither model is an authority on its own accuracy.

The orchestrator owns persistent decisions, project slots, resource accounting, job dispatch and recovery. Routine research proceeds within the admitted mission and existing access permissions. This policy does not grant additional permissions for spending, publication, deployment or physical operations.

For admission, switching, continuation and completion, preserve the relevant project and job identifiers, policy and taxonomy revisions, evidence references, rationale, material unknowns, decision author or model identity, and next action or reopening condition. Keep records proportionate and reuse existing assessments when they remain valid.

Completion applies to the stated deliverable. A completed feasibility study may still leave a physical design unvalidated. Further development requires an explicit follow-up decision; completed work must not silently expand into an indefinite new mission.

Assess success through demonstrated engineering improvements, useful uncertainties resolved, burdens reduced and usable outputs. Project counts, document volume, token consumption and GPU utilisation are operational measurements, not substitutes for those outcomes.

9. Maintainer decisions

2026-10-05:

Admission is autonomous under rubric v1 (section 4a), with no human approval step.

Projects run in parallel, limited by Spark health and spare capacity (section 5). This replaces the initial limit of one development project and one feasibility investigation.

Research steps are short and are not preempted. PlantScope may wait for a running step to finish, for at most about ten minutes; Apollo enforces that limit on each step.

Priority follows the Maslow layer, need severity and reach, effectiveness, ease of use and implementation, cost and practicality, weighted as in section 4a.

2026-10-06:

The product of Applied Commons research is a catalogue of open solutions to human needs, with full build packs (bill of materials, design files, assembly and test) for the compelling ones.

The need is established first (need briefs, section 4a); the existing screening and admission stay as the first pass; a go/no-go review gates the build pack (section 4b), so substantial compute follows a compelling reason.

The build gate is automatic, with maintainer override (section 4d).

Design files are kept as pinned local copies when their licence allows (section 4c).

2026-10-07:

Software and data projects stay in the catalogue. Their build pack documents a working deployment: the hardware, hosting and services needed with their costs; source repositories, data and configuration; installation and deployment; and verification of a running instance.

Enablers are a track alongside the layers (section 2a), starting with Energy, Communications and information, and Environment and climate, then Fabrication and tools, Transport and logistics, Instrumentation and control, Skills and know-how (including maintenance and repair), Computing and open data, Cold chain and storage, Mapping and geospatial, Finance and payments, and Materials and circularity; more may be added. Environmental enablers and the environmental Safety need both stay. Enablers compete equally for slots, with a slight attention bump.

Applied Commons progresses beyond research to physical trials. The maintainer's 3 ha in Estonia is the proving ground, used as appropriate for each candidate; it does not limit work to the Estonian climate. Cold-climate, year-round solutions are a target alongside the others (section 2b).

Before trials, a modular set (section 4e): interface standards approved by the maintainer, modules built on them, systems for five scenarios (single-family household, homestead, small farm, community facility, village) and a roadmap revised monthly and approved by the maintainer. The cheaper the better, so results stay usable in low-income countries.

Proposed standards are verified against established standards (ISO, IEC, EN/DIN, AS/NZS) before approval, with deviations called out (section 4e): nothing unsafe, without heavy overhead.

The published catalogue is navigable by layer, category and project, with related projects linked across categories (section 4f).

Applied Commons is primarily for the public good. Apollo Automation (the maintainer's company) may take on projects where there is an opportunity; that does not change how work is prioritised here.

A need is done when modules tested in the real world substantially solve the problem its brief describes; continuous improvement (stage 4) then closes the remaining gaps.

Applied Commons' own work is open and may be used commercially: software under GPL-3.0-or-later, hardware designs under CERN-OHL-S-2.0, documentation under CC BY-SA 4.0. Modules and systems are built only on designs whose licences allow modification and commercial use; a module derived from a share-alike design (such as CC BY-SA or GPL) keeps that design's licence. Non-commercial or unlicensed projects stay in the catalogue but are not built on.

2026-10-21:

Applied Commons adopts the Civilisation Capability Graph (capability-graph.md) as an extension below the categories (section 4k). The Maslow layers, the categories and project selection stay as they are; need, baseline and improvement remain first-class.

Graph candidates are reviewed by an assistant, safety-critical and medical ones also get an independent check, and the maintainer approves them in batches.

2026-10-23:

The maintainer can make projects a priority (section 4d): they are admitted once assessed and their work is taken up first, while research and the go/no-go review still decide whether they are built. First priorities: aquaponics, vertical farming, PAROL6, WikiHouse and Maslow CNC.

Duplicate projects are merged: a project is catalogued once and listed under every need it serves (section 4d).

Non-commercial designs (later on 2026-10-23). Applied Commons is a non-commercial initiative. Design files under non-commercial licences (such as CC-BY-NC or CC-BY-NC-SA) may be archived and published as Applied Commons copies, clearly marked as non-commercial wherever they are listed or offered. For building, open designs (changes and commercial use allowed) are used wherever possible, and non-commercial designs are used as references wherever possible: their measurements, methods and failure modes inform Applied Commons' own open designs. A non-commercial design may still be built on, sparingly, when no open alternative exists. The derivative keeps the source's licence and is clearly labelled non-commercial, and so is everything derived from it in turn. It is not combined with GPL or CERN-OHL-S parts, whose licences conflict; module synthesis applies this to every share-alike source (CC-BY-SA and the like too), since each forbids the added restriction, and refuses a module built on a non-commercial design that does not say why no open design serves. No-derivatives and unknown licences are still neither copied nor built on. This refines the 2026-10-07 decision that modules build only on open or share-alike designs.

The public website moves from a wiki-style presentation to a needs-led engineering capability platform (maintainer dev instruction, 2026-10-23, kept in the Apollo repository as docs/ac-public-interface-redesign.md). The Maslow hierarchy stays the primary navigation (layer, category, capability, solutions, build packs), with secondary classifications. The site stays static on GitHub Pages and generated from the canonical AC records, with no second project database. Development maturity, evidence confidence and build-pack completeness are shown separately, missing information is labelled unknown, and licences stay visible. The work is incremental, starting with an audit, and the current site stays up until its replacement meets the acceptance criteria. This replaces the earlier aim of a Wikipedia-like wiki.

2026-10-25:

The production library (section 4l): Applied Commons aims to decentralise and reinvent the means of production, starting with a small community farm and workshop connected to regional specialist producers. Applied Commons Production Library v0.4 is staged beside the canonical records, idempotently and without overwriting reviewed records; its capabilities and systems are proposals until reconciled. The public site opens the library on its eight local service systems, while Maslow needs stay the primary navigation.
