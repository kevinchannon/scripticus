from scripticus_schema.index_api import (
    PackageSummary,
    PackageVersions,
    SearchResults,
    VersionSummary,
)


def test_version_summary_defaults_to_not_yanked():
    assert VersionSummary(version="1.2.3").yanked is False


def test_package_versions_round_trips_through_json():
    detail = PackageVersions(
        namespace="kevin-c",
        name="my-tool",
        description="A tool",
        versions=[
            VersionSummary(version="2.0.0"),
            VersionSummary(version="1.0.0", yanked=True),
        ],
    )
    assert PackageVersions.model_validate_json(detail.model_dump_json()) == detail


def test_search_results_default_to_empty():
    assert SearchResults().results == []


def test_search_results_round_trip_through_json():
    results = SearchResults(
        results=[
            PackageSummary(namespace="kevin-c", name="my-tool", latest_version="2.0.0")
        ]
    )
    assert SearchResults.model_validate_json(results.model_dump_json()) == results
