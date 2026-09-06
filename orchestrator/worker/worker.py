import json
import os
import time
from typing import Any

import httpx


API_URL = os.getenv("API_URL", "http://api:8000")
QWEN_URL = os.getenv("QWEN_URL", "http://qwen:8080")
QWEN_MODEL = os.getenv("QWEN_MODEL", "qwen3-1.7b")
WORKER_NAME = os.getenv("WORKER_NAME", "nuc-worker-01")

POLL_INTERVAL = 5

JOB_TYPES = [
    "smoke_test",
    "classify",
    "organise",
    "summarise",
    "question_generate",
    "deduplicate",
]


SYSTEM_PROMPT = """
You are the lightweight orchestration model for Applied Commons.

Applied Commons develops practical, reproducible, open engineering solutions
for fundamental human needs.

Your role is to organise research, not to invent unsupported facts.

Rules:
- Be concise.
- Do not fabricate sources, evidence, measurements, or test results.
- Distinguish evidence from proposals.
- Prefer lower Maslow layers when categorising engineering work.
- Preserve technical uncertainty.
- If information is insufficient, say so.
- Do not claim that a hypothesis has been validated unless evidence is supplied.
""".strip()


JOB_PROMPTS = {
    "classify": """
Classify the supplied engineering item.

Identify:
1. Primary Maslow level from 1 to 5.
2. Primary engineering domain.
3. Important cross-cutting capabilities.
4. A short justification.

Return concise plain text.
""",

    "organise": """
Organise the supplied material into a clear engineering research structure.

Identify:
- main topic
- subtopics
- dependencies
- unresolved questions
- suggested next action

Do not add unsupported technical claims.
""",

    "summarise": """
Summarise the supplied material for an engineering research programme.

Preserve:
- important facts
- quantitative values
- assumptions
- uncertainties
- unresolved questions

Remove repetition and speculation that is not supported by the supplied material.
""",

    "question_generate": """
Generate useful follow-up engineering research questions from the supplied
material.

Prioritise questions that are:
- testable
- practically important
- relevant to fundamental human needs
- feasible for open-source engineering
- capable of producing measurable evidence

Avoid vague questions and avoid generating an unnecessarily large research tree.

Return no more than five candidate questions.
""",

    "deduplicate": """
Assess whether the supplied research items substantially duplicate one another.

Identify:
- duplicates
- near-duplicates
- genuinely distinct items
- recommended merged wording where appropriate

Do not merge questions merely because they belong to the same topic.
""",
}


def claim_job(client: httpx.Client):
    response = client.post(
        f"{API_URL}/jobs/claim",
        json={
            "worker": WORKER_NAME,
            "job_types": JOB_TYPES,
        },
    )
    response.raise_for_status()
    return response.json().get("job")


def call_qwen(
    client: httpx.Client,
    job_type: str,
    input_data: dict[str, Any],
) -> dict[str, Any]:
    task_prompt = JOB_PROMPTS[job_type]

    user_content = (
        "/no_think\n\n"
        f"{task_prompt}\n\n"
        "SUPPLIED DATA:\n"
        f"{json.dumps(input_data, indent=2, ensure_ascii=False)}"
    )

    response = client.post(
        f"{QWEN_URL}/v1/chat/completions",
        json={
            "model": QWEN_MODEL,
            "messages": [
                {
                    "role": "system",
                    "content": SYSTEM_PROMPT,
                },
                {
                    "role": "user",
                    "content": user_content,
                },
            ],
            "temperature": 0.1,
            "max_tokens": 384,
        },
        timeout=120.0,
    )

    response.raise_for_status()

    payload = response.json()

    content = payload["choices"][0]["message"]["content"]

    return {
        "model": QWEN_MODEL,
        "job_type": job_type,
        "response": content,
        "usage": payload.get("usage"),
    }


def execute(
    api_client: httpx.Client,
    qwen_client: httpx.Client,
    job: dict[str, Any],
) -> dict[str, Any]:
    job_type = job["job_type"]

    if job_type == "smoke_test":
        return {
            "result": "worker pipeline operational",
            "worker": WORKER_NAME,
            "input": job.get("input", {}),
        }

    if job_type in JOB_PROMPTS:
        return call_qwen(
            qwen_client,
            job_type,
            job.get("input", {}),
        )

    raise ValueError(
        f"Unsupported job type: {job_type}"
    )


def complete_job(
    client: httpx.Client,
    job: dict[str, Any],
    output: dict[str, Any],
):
    response = client.post(
        f"{API_URL}/jobs/{job['id']}/complete",
        json={
            "worker": WORKER_NAME,
            "output": output,
        },
    )

    response.raise_for_status()


def fail_job(
    client: httpx.Client,
    job: dict[str, Any],
    error: Exception,
):
    response = client.post(
        f"{API_URL}/jobs/{job['id']}/fail",
        json={
            "worker": WORKER_NAME,
            "error": str(error),
        },
    )

    response.raise_for_status()


def main():
    print(f"{WORKER_NAME} starting")
    print(f"API: {API_URL}")
    print(f"Qwen: {QWEN_URL}")
    print(f"Model: {QWEN_MODEL}")
    print(f"Job types: {JOB_TYPES}")

    with (
        httpx.Client(timeout=30.0) as api_client,
        httpx.Client(timeout=120.0) as qwen_client,
    ):
        while True:
            try:
                job = claim_job(api_client)

                if job is None:
                    time.sleep(POLL_INTERVAL)
                    continue

                print(
                    f"Claimed job {job['id']} "
                    f"type={job['job_type']}"
                )

                try:
                    output = execute(
                        api_client,
                        qwen_client,
                        job,
                    )

                    complete_job(
                        api_client,
                        job,
                        output,
                    )

                    print(
                        f"Completed job {job['id']}"
                    )

                except Exception as exc:
                    print(
                        f"Job {job['id']} failed: {exc}"
                    )

                    fail_job(
                        api_client,
                        job,
                        exc,
                    )

            except Exception as exc:
                print(
                    f"Worker loop error: {exc}"
                )

                time.sleep(POLL_INTERVAL)


if __name__ == "__main__":
    main()
