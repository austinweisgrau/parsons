import logging

import requests

from parsons.etl import Table
from parsons.utilities.oauth_api_connector import OAuth2APIConnector

logger = logging.getLogger(__name__)


class Reach:
    base_url: str = "https://api.reach.vote/"

    def __init__(self, username: str, password: str) -> None:
        self.client = OAuth2APIConnector(
            self.base_url,
            client_id=username,
            client_secret=password,
            token_url=self.base_url + "oauth/token",
        )

    def fetch_token(self, username: str, password: str) -> str:
        response = self.client.post_request(
            "oauth/token",
            data={"username": username, "password": password},
        )
        result = response.json()["access_token"]
        return result

    def get_tags(self) -> list[dict]:
        return self.request("get", "api/v1/tags").json()["tags"]

    def create_tag(
        self, tag_name: str, description: str | None = None
    ) -> requests.Response:
        payload = {"name": tag_name}
        if description:
            payload["description"] = description
        response = self.request("post", "api/v1/tags", data=payload)
        if not response.status_code >= 200 and response.status_code < 300:
            raise RuntimeError(
                f"Create tag failed. [status_code={response.status_code}, "
                f"reason={response.reason}]"
            )
        else:
            return response

    def import_tags(
        self, table: Table, tag_id: str, s3_temp_bucket: str | None = None
    ) -> requests.Response:
        if not s3_temp_bucket:
            s3_temp_bucket = os.environ["S3_TEMP_BUCKET"]

        filepath = tempfile.mkstemp()[1]
        table.to_csv(filepath)

        s3_key = "/".join(
            ["reach", datetime.datetime.today().strftime("%Y-%m-%d"), str(uuid.uuid4())]
        )

        boto3.client("s3").upload_file(filepath, s3_temp_bucket, s3_key)

        presigned_url = boto3.client("s3").generate_presigned_url(
            "get_object",
            Params={"Bucket": s3_temp_bucket, "Key": s3_key},
            ExpiresIn=3600,
        )

        response = self.request(
            "post", f"api/v1/imports/tags/{tag_id}", data={"file_url": presigned_url}
        )

        if not response.status_code >= 200 and response.status_code < 300:
            raise RuntimeError(
                f"Bulk import failed. [status_code={response.status_code}, "
                f"reason={response.reason}, data={response.json()}]"
            )

        response_data = response.json()

        job_id = response_data["data"]["id"]
        status = response_data["data"]["status"]

        while status == "in_progress":
            time.sleep(5)
            response = self.request("get", f"api/v1/imports/tags/{job_id}")
            response_data = response.json()
            status = response_data["data"]["status"]
            get_logger().info(
                f"Fetched status of Reach import job. [id={job_id}, "
                f"status={status}]"
            )

        if status == "completed":
            get_logger().info(
                f"Completed bulk import of tags in Reach. [source_rows={table.num_rows}, "
                f"tagged_rows={response_data['meta']['success_rows']}]"
            )
        else:
            raise RuntimeError(
                f"Something went wrong. [job_id={job_id}, status={status}, "
                f"data={response_data}]"
            )
