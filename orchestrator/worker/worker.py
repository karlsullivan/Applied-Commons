import os
import time
import httpx


API_URL = os.getenv("API_URL", "http://api:8000")
WORKER_NAME = os.getenv("WORKER_NAME", "nuc-worker-01")

JOB_TYPES = [
    "smoke_test",
]


def claim_job(client):
    response = client.post(
        f"{API_URL}/jobs/claim",
        json={
            "worker": WORKER_NAME,
            "job_types": JOB_TYPES,
        },
    )
    response.raise_for_status()
    return response.json().get("job")


def execute(job):
    job_type = job["job_type"]

    if job_type == "smoke_test":
        return {
            "result": "worker pipeline operational",
            "worker": WORKER_NAME,
            "input": job.get("input", {}),
        }

    raise ValueError(f"Unsupported job type: {job_type}")


def complete_job(client, job, output):
    response = client.post(
        f"{API_URL}/jobs/{job['id']}/complete",
        json={
            "worker": WORKER_NAME,
            "output": output,
        },
    )
    response.raise_for_status()


def main():
    print(f"{WORKER_NAME} starting")
    print(f"API: {API_URL}")
    print(f"Job types: {JOB_TYPES}")

    with httpx.Client(timeout=30.0) as client:
        while True:
            try:
                job = claim_job(client)

                if job is None:
                    time.sleep(5)
                    continue

                print(
                    f"Claimed job {job['id']} "
                    f"type={job['job_type']}"
                )

                output = execute(job)
                complete_job(client, job, output)

                print(f"Completed job {job['id']}")

            except Exception as exc:
                print(f"Worker error: {exc}")
                time.sleep(5)


if __name__ == "__main__":
    main()
