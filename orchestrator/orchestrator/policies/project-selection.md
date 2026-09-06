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

5. Focus and project limits

The initial portfolio allows:

One active engineering development project.

One separate feasibility investigation with a defined question, deliverable and checkpoint.

These limits apply across Applied Commons as a whole. Categories do not receive independent project allowances. Subprojects, supporting tools and follow-up questions retain their parent project's scope and resource accounting. A distinct independent outcome requires a separate admission decision.

Keep a finite, deduplicated shortlist of candidates. Discovery must have an explicit scope and purpose, and remain subordinate to useful progress on admitted work. Do not generate or investigate projects merely to populate categories or occupy available compute.

Admission to an occupied development slot requires an explicit completion or parking decision for the current project. Record the reason for a switch and preserve the existing work. Temporary PlantScope interruptions retain the current project's place. Reopening parked work requires new evidence, changed constraints, a resolved blocker or a recorded maintainer decision.

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
