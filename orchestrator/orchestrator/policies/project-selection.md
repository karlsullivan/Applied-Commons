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

Some work is not a need in itself but something many needs depend on: energy, communications and information, environment and climate (taxonomy.md, Enablers). Enablers form a track alongside the layers rather than a layer of their own. Each enabler category has its own need brief, framed by the essential needs that depend on it, and its own discovery.

An enabler project is screened on the needs it serves: its assessment lists the physiological and safety need categories it directly makes easier to meet, and the project takes the most basic layer among them for the need-layer score. The other dimensions are scored on those needs. Environment and climate as an enabler (carbon removal, ecosystem restoration, adaptation) is distinct from the Safety category Environmental stability and resource security (exposure and access), and both remain.

Enablers compete for the same slots under the same rubric and thresholds, with a slight attention bump: their pipeline steps queue a little ahead, and admission orders them as if their composite were 0.05 higher. The recorded composite is not changed.

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

Design files are kept as pinned local copies (ops/archive/archive_designs.py): a repository at its recorded commit and direct file downloads, only when the repository's licence file or the reported licence is a recognised open licence; anything else stays a link. The archiver fetches only https sources on public addresses.

4d. Maintainer steering

The maintainer may steer any project: build forces a build pack (admitting a candidate if needed, outside the slot limit), park parks it. A steer is recorded as a decision with author "maintainer (steer)" and acts once; clearing it does not undo what it caused.

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

Enablers are a track alongside the layers (section 2a), starting with Energy, Communications and information, and Environment and climate; more may be added. Environmental enablers and the environmental Safety need both stay. Enablers compete equally for slots, with a slight attention bump.
