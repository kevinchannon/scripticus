from scripticus_schema.publish_api import PublishedArtifact, PublishResult


def test_publish_result_round_trips_through_json():
    result = PublishResult(
        namespace="kevin-c",
        name="my-tool",
        version="1.0.0",
        content_hash="sha256:abc123",
        publisher="kevin-c",
        artifacts=[
            PublishedArtifact(
                filename="my_tool-1.0.0-linux.macos-bash.tar.gz",
                archive_format="tar.gz",
                platforms=["linux", "macos"],
                language="bash",
                size=1234,
            )
        ],
    )
    assert PublishResult.model_validate_json(result.model_dump_json()) == result
