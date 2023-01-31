"""Tests for the Starbase utility functions

This tests all the Starbase utility functions

:Module: starfleet.tests.starbase.test_utils
:Copyright: (c) 2023 by Gemini Trust Company, LLC., see AUTHORS for more info
:License: See the LICENSE file for details
:Author: Mike Grima <michael.grima@gemini.com>
"""
# pylint: disable=unused-argument
from typing import Set
from unittest import mock

import pytest
from botocore.client import BaseClient
from botocore.exceptions import ClientError
from yaml import YAMLError


def test_get_template_batch() -> None:
    """This tests that we have the correct template batching logic."""
    from starfleet.starbase.utils import get_template_batch

    template_list = [f"template{x}" for x in range(1, 28)]  # Creates 27 items to test that we iterate a few times and cover all items.
    batches = list(get_template_batch(template_list, "testing"))
    total_sum = 0
    for batch in batches:
        total_sum += len(batch)

    assert len(batches) == 3
    assert total_sum == len(template_list) == 27
    assert batches[0][0] == {"Id": "1", "MessageBody": '{"worker_ship": "testing", "template_prefix": "template1"}'}
    assert batches[2][6] == {"Id": "27", "MessageBody": '{"worker_ship": "testing", "template_prefix": "template27"}'}


def test_starbase_fanout(aws_sqs: BaseClient, fanout_queue: str) -> None:
    """This tests the logic for the Starbase adding items to the fanout queue."""
    from starfleet.starbase.utils import task_starbase_fanout

    template_list = [f"template{x}" for x in range(1, 3)]  # Just create 2
    task_starbase_fanout(template_list, fanout_queue, aws_sqs, "testing")

    # Verify the messages appeared in SQS:
    messages = [message["Body"] for message in aws_sqs.receive_message(QueueUrl=fanout_queue, MaxNumberOfMessages=10)["Messages"]]
    assert len(messages) == 2

    # The messages are not guaranteed to be in the same order:
    for template_num in range(1, 3):
        assert '{"worker_ship": "testing", "template_prefix": "templateCOUNT"}'.replace("COUNT", str(template_num)) in messages


def test_list_worker_ship_templates(aws_s3: BaseClient, payload_templates: Set[str], template_bucket: str) -> None:
    """This tests the logic for getting a worker ship template -- both from S3 and not S3."""
    from starfleet.starbase.utils import list_worker_ship_templates

    # Put some non-yaml files in the bucket too: these should not be fetched!
    aws_s3.put_object(Bucket=template_bucket, Key="TestingStarfleetWorkerPlugin/NotATemplate.notayaml", Body="nope".encode("utf-8"))

    # Test with fetching from S3 first:
    templates = set(list_worker_ship_templates(template_bucket, "us-east-2", "TestingStarfleetWorkerPlugin/", "TestingStarfleetWorkerPlugin"))
    assert templates == {
        "TestingStarfleetWorkerPlugin/template1.yaml",
        "TestingStarfleetWorkerPlugin/template2.yaml",
    }  # Confirms the exact response; should not contain the NotATemplate.notayaml file.

    # Next, try without anything related to S3:
    with mock.patch("starfleet.starbase.utils.boto3") as mocked_boto:
        templates = list_worker_ship_templates(template_bucket, "us-east-2", "TestingStarfleetWorkerPlugin/template1.yaml", "TestingStarfleetWorkerPlugin")
        assert templates == ["TestingStarfleetWorkerPlugin/template1.yaml"]
        assert not mocked_boto.client.called  # Verify that client = boto3.client(... was not called.


def test_fetch_template(aws_s3: BaseClient, payload_templates: Set[str], template_bucket: str) -> None:
    """This tests that we are able to properly fetch templates from S3."""
    from starfleet.starbase.utils import fetch_template

    template = fetch_template(aws_s3, template_bucket, "TestingStarfleetWorkerPlugin/template1.yaml")  # noqa
    assert template == {"TemplateName": "TestWorkerTemplate", "TemplateDescription": "This is a template used for testing the Starbase"}

    # S3 exceptions:
    with mock.patch("starfleet.starbase.utils.LOGGER") as mocked_logger:
        # With a file that is not in S3 (for whatever reason):
        with pytest.raises(ClientError):
            template = fetch_template(aws_s3, template_bucket, "LOLNO")  # noqa
        assert mocked_logger.error.call_args.args[0] == "[❌] Can't find the template: LOLNO in S3. Please investigate why it's missing."
        mocked_logger.reset_mock()

        # Some other odd S3 exception (wrong bucket):
        with pytest.raises(ClientError):
            template = fetch_template(aws_s3, "fakebucket", "LOLNO")  # noqa
        assert mocked_logger.error.call_args.args[0].startswith("[❌] Some problem occurred reaching out to S3 (NoSuchBucket) while fetching: LOLNO.")

    # Not a YAML:
    aws_s3.put_object(Bucket=template_bucket, Key="notyaml", Body=b"\xc62:\xe3\xc5\x93\n\xe1\xf0\xd5\xe4[")  # random binary blob generated by os.urandom(12)
    with pytest.raises(YAMLError):
        fetch_template(aws_s3, template_bucket, "notyaml")  # noqa
