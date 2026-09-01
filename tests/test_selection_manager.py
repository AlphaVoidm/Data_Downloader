"""Tests for the Selection Manager module."""
from __future__ import annotations

import pytest

from selection_manager import (
    MODE_AUTOMATIC, MODE_MANUAL,
    get_feature_groups, get_source_groups,
    build_download_plan, validate_selection, render_plan_preview,
    get_available_countries,
)


class TestFeatureGroups:
    def test_feature_groups_returns_all_tiers(self):
        groups = get_feature_groups()
        assert "TARGET" in groups
        assert "CORE" in groups
        assert "EXTENDED" in groups
        assert "OPTIONAL" in groups

    def test_target_has_electricity_demand(self):
        groups = get_feature_groups()
        target_features = [f["concept"] for f in groups["TARGET"]["features"]]
        assert "electricity_demand" in target_features

    def test_core_has_climate_and_economic(self):
        groups = get_feature_groups()
        core_features = [f["concept"] for f in groups["CORE"]["features"]]
        assert "temperature_2m" in core_features
        assert "gdp" in core_features
        assert "total_population" in core_features

    def test_each_feature_has_required_fields(self):
        groups = get_feature_groups()
        for group_key, group in groups.items():
            for feat in group["features"]:
                assert "concept" in feat
                assert "name" in feat
                assert "domain" in feat
                assert "frequency" in feat
                assert "unit" in feat
                assert "sources" in feat


class TestSourceGroups:
    def test_source_groups_not_empty(self):
        groups = get_source_groups()
        assert len(groups) > 0

    def test_electricity_demand_sources_exist(self):
        groups = get_source_groups()
        # At least one domain should have demand sources
        all_source_ids = []
        for sources in groups.values():
            for s in sources:
                all_source_ids.append(s["source_id"])
        assert "ember" in all_source_ids
        assert "entsoe" in all_source_ids

    def test_each_source_has_required_fields(self):
        groups = get_source_groups()
        for domain, sources in groups.items():
            for src in sources:
                assert "source_id" in src
                assert "source_name" in src
                assert "features" in src
                assert "auth_required" in src


class TestGetAvailableCountries:
    def test_countries_not_empty(self):
        countries = get_available_countries()
        assert len(countries) > 0

    def test_countries_have_iso3(self):
        countries = get_available_countries()
        for c in countries:
            assert "iso3" in c
            assert len(c["iso3"]) == 3

    def test_known_countries_present(self):
        countries = get_available_countries()
        iso3s = [c["iso3"] for c in countries]
        assert "USA" in iso3s
        assert "GBR" in iso3s
        assert "DEU" in iso3s
        assert "EGY" in iso3s


class TestBuildDownloadPlan:
    def test_basic_plan(self):
        plan = build_download_plan(
            countries=["DEU"],
            features=["electricity_demand", "temperature_2m"],
            start_year=2020,
            end_year=2023,
        )
        assert plan.country_count == 1
        assert plan.feature_count == 2
        assert len(plan.countries) == 1
        assert plan.countries[0].iso3 == "DEU"

    def test_plan_has_selections(self):
        plan = build_download_plan(
            countries=["EGY"],
            features=["electricity_demand"],
            start_year=2020,
            end_year=2023,
        )
        assert len(plan.countries[0].selections) == 1
        sel = plan.countries[0].selections[0]
        assert sel.feature_concept == "electricity_demand"
        assert sel.source_id != ""  # should resolve to a source

    def test_automatic_mode_selects_best_source(self):
        plan = build_download_plan(
            countries=["GBR"],
            features=["electricity_demand"],
            start_year=2020,
            end_year=2023,
            source_mode=MODE_AUTOMATIC,
        )
        sel = plan.countries[0].selections[0]
        # GBR should get NESO or ENTSO-E in automatic mode
        assert sel.source_id in ("neso", "entsoe", "ember")

    def test_manual_mode_with_override(self):
        plan = build_download_plan(
            countries=["GBR"],
            features=["electricity_demand"],
            start_year=2020,
            end_year=2023,
            source_mode=MODE_MANUAL,
            source_overrides={"electricity_demand": "ember"},
        )
        sel = plan.countries[0].selections[0]
        assert sel.source_id == "ember"

    def test_multiple_countries(self):
        plan = build_download_plan(
            countries=["USA", "DEU", "EGY"],
            features=["temperature_2m"],
            start_year=2020,
            end_year=2023,
        )
        assert plan.country_count == 3
        # All should get NASA POWER or ERA5 for temperature
        for cp in plan.countries:
            assert len(cp.selections) == 1
            assert cp.selections[0].feature_concept == "temperature_2m"


class TestValidateSelection:
    def test_valid_selection(self):
        result = validate_selection(
            countries=["DEU"],
            features=["temperature_2m"],  # NASA POWER - no auth needed
            start_year=2020,
            end_year=2023,
        )
        assert result["valid"] is True
        assert len(result["errors"]) == 0

    def test_no_countries_error(self):
        result = validate_selection(
            countries=[],
            features=["electricity_demand"],
            start_year=2020,
            end_year=2023,
        )
        assert result["valid"] is False
        assert any("No countries" in e for e in result["errors"])

    def test_no_features_error(self):
        result = validate_selection(
            countries=["DEU"],
            features=[],
            start_year=2020,
            end_year=2023,
        )
        assert result["valid"] is False
        assert any("No features" in e for e in result["errors"])

    def test_bad_period_error(self):
        result = validate_selection(
            countries=["DEU"],
            features=["electricity_demand"],
            start_year=2025,
            end_year=2020,
        )
        assert result["valid"] is False

    def test_summary_has_all_keys(self):
        result = validate_selection(
            countries=["DEU"],
            features=["electricity_demand", "temperature_2m"],
            start_year=2020,
            end_year=2023,
        )
        summary = result["summary"]
        assert "countries" in summary
        assert "features" in summary
        assert "sources" in summary
        assert "requests" in summary
        assert "auth_issues" in summary


class TestRenderPlanPreview:
    def test_preview_not_empty(self):
        plan = build_download_plan(
            countries=["DEU"],
            features=["electricity_demand"],
            start_year=2020,
            end_year=2023,
        )
        preview = render_plan_preview(plan)
        assert len(preview) > 0
        assert "DOWNLOAD PLAN" in preview
        assert "DEU" in preview

    def test_preview_contains_country(self):
        plan = build_download_plan(
            countries=["EGY", "DEU"],
            features=["temperature_2m"],
            start_year=2020,
            end_year=2023,
        )
        preview = render_plan_preview(plan)
        assert "EGY" in preview
        assert "DEU" in preview
