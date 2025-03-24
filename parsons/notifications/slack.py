import os
import time
import warnings
from typing import Optional

import requests
from slack_sdk.errors import SlackApiError
from slacksdk import WebClient

from parsons.etl.table import Table
from parsons.utilities.check_env import check


class Slack:
    def __init__(self, api_key: str = None):
        if api_key is None:
            try:
                self.client = WebClient(token=os.environ["SLACK_API_TOKEN"])
            except KeyError:
                raise KeyError(
                    "Missing api_key. It must be passed as an "
                    "argument or stored as environmental variable"
                )
        else:
            self.api_key = api_key

        self.client = WebClient(token=self.api_key)

    def channels(
        self,
        fields=["id", "name"],
        exclude_archived: bool = False,
        types: list[str] = ["public_channel"],
    ) -> Table:
        """
        Return a list of all channels in a Slack team.

        `Args:`
            fields: list
                A list of the fields to return. By default, only the channel
                `id` and `name` are returned. See
                https://api.slack.com/methods/conversations.list for a full
                list of available fields. `Notes:` nested fields are unpacked.
            exclude_archived: bool
                Set to `True` to exclude archived channels from the list.
                Default is false.
            types: list
                Mix and match channel types by providing a list of any
                combination of `public_channel`, `private_channel`,
                `mpim` (aka group messages), or `im` (aka 1-1 messages).
        `Returns:`
            Parsons Table
                See :ref:`parsons-table` for output options.
        """
        tbl = self._paginate_request(
            "conversations.list",
            "channels",
            types=types,
            exclude_archived=exclude_archived,
        )

        tbl.unpack_dict("topic", include_original=False, prepend=True, prepend_value="topic")
        tbl.unpack_dict("purpose", include_original=False, prepend=True, prepend_value="purpose")

        rm_cols = [x for x in tbl.columns if x not in fields]
        tbl.remove_column(*rm_cols)

        return tbl

    def users(
        self,
        fields: list[str] = [
            "id",
            "name",
            "deleted",
            "profile_real_name_normalized",
            "profile_email",
        ],
    ) -> Table:
        """
        Return a list of all users in a Slack team.

        `Args:`
            fields: list
                A list of the fields to return. By default, only the user
                `id` and `name` and `deleted` status are returned. See
                https://api.slack.com/methods/users.list for a full list of
                available fields. `Notes:` nested fields are unpacked.
        `Returns:`
            Parsons Table
                See :ref:`parsons-table` for output options.
        """

        tbl = self._paginate_request("users.list", "members", include_locale=True)

        tbl.unpack_dict("profile", include_original=False, prepend=True, prepend_value="profile")

        rm_cols = [x for x in tbl.columns if x not in fields]
        tbl.remove_column(*rm_cols)

        return tbl

    @classmethod
    def message(
        cls,
        channel: str,
        text: str,
        webhook: Optional[str] = None,
        parent_message_id: Optional[str] = None,
    ):
        """
        Send a message to a Slack channel with a webhook instead of an api_key.
        You might not have the full-access API key but still want to notify a channel
        `Args:`
            channel: str
                The name or id of a `public_channel`, a `private_channel`, or
                an `im` (aka 1-1 message).
            text: str
                Text of the message to send.
            webhook: str
                If you have a webhook url instead of an api_key
                Looks like: https://hooks.slack.com/services/Txxxxxxx/Bxxxxxx/Dxxxxxxx
            parent_message_id: str
                The `ts` value of the parent message. If used, this will thread the message.
        """
        webhook = check("SLACK_API_WEBHOOK", webhook, optional=True)
        payload = {"channel": channel, "text": text}
        if parent_message_id:
            payload["thread_ts"] = parent_message_id
        return requests.post(webhook, json=payload)

    def message_channel(
        self, channel: str, text: str, parent_message_id: Optional[str] = None, **kwargs
    ):
        """
        Send a message to a Slack channel

        `Args:`
            channel: str
                The name or id of a `public_channel`, a `private_channel`, or
                an `im` (aka 1-1 message).
            text: str
                Text of the message to send.
            parent_message_id: str
                The `ts` value of the parent message. If used, this will thread the message.
            **kwargs: kwargs
                as_user: str
                    This is a deprecated argument. Use optional username, icon_url, and icon_emoji
                    args to customize the attributes of the user posting the message.
                    See https://api.slack.com/methods/chat.postMessage#legacy_authorship for
                    more information about legacy authorship
                Additional arguments for chat.postMessage API call. See documentation
                <https://api.slack.com/methods/chat.postMessage>` for more info.


        `Returns:`
            `dict`:
                A response json
        """

        if "as_user" in kwargs:
            warnings.warn(
                "as_user is a deprecated argument on message_channel().",
                DeprecationWarning,
                stacklevel=2,
            )
        if "thread_ts" in kwargs:
            warnings.warn(
                "thread_ts argument on message_channel() will be ignored. Use parent_message_id.",
                Warning,
                stacklevel=2,
            )
            kwargs.pop("thread_ts", None)

        response = self._request_with_rate_limit_retry(
            self.client.chat_postMessage,
            channel=channel,
            text=text,
            thread_ts=parent_message_id,
            **kwargs,
        )
        return response

    def upload_file(
        self,
        channels: list[str],
        filename: str,
        initial_comment: Optional[str] = None,
        title: Optional[str] = None,
        is_binary: None = None,
        filetype: None = None,
    ) -> list[dict]:
        """
        Upload a file to Slack channel(s).

        `Args:`
            channels: list
                The list of channel names or IDs where the file will be shared.
            filename: str
                The path to the file to be uploaded.
            initial_comment: str
                The text of the message to send along with the file.
            title: str
                Title of the file to be uploaded.
            is_binary: None
                This argument is deprecated.
            filetype: None
                This argument is deprecated.
        `Returns:`
            `list[dict]`:
                A list of response jsons
        """
        if filetype is not None:
            warnings.warn(
                "`filetype` is a deprecated argument and will be removed in a future Parsons version.",
                DeprecationWarning,
            )
        if is_binary is not None:
            warnings.warn(
                "`is_binary` is a deprecated argument and will be removed in a future Parsons version.",
                DeprecationWarning,
            )
        responses = []
        for channel in channels:
            response = self._request_with_rate_limit_retry(
                self.client.files_upload_v2,
                channel=channel,
                file=filename,
                initial_comment=initial_comment,
                title=title,
            )
            responses.append(response)
        return responses

    def _paginate_request(self, endpoint: str, collection: str, **kwargs) -> Table:
        # The max object we're requesting at a time.
        # This is an nternal limit to not overload slack api
        LIMIT = 200

        items = []
        next_page = True
        cursor = None
        while next_page:
            response = self._request_with_rate_limit_retry(
                self.client.api_call,
                endpoint=endpoint,
                cursor=cursor,
                limit=LIMIT,
                **kwargs,
            )

            items.extend(response[collection])

            if response["response_metadata"]["next_cursor"]:
                cursor = response["response_metadata"]["next_cursor"]
            else:
                next_page = False

        return Table(items)

    def _request_with_rate_limit_retry(self, method, *args, **kwargs) -> dict:
        """Make a request to the Slack API and retry if rate limited."""
        try:
            response = method(*args, **kwargs)
        except SlackApiError as e:
            if e.response.status_code == 429:
                delay = int(e.response.headers["Retry-After"])
                print(f"Rate limited. Retrying in {delay} seconds")
                time.sleep(delay)
                response = method(*args, **kwargs)
            else:
                raise
        return response
