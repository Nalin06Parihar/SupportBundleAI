"""Streamlit dashboard for the SupportBundleAI FastAPI workflow."""

import os
from typing import Any

import httpx
import streamlit as st

API_BASE_URL = os.environ.get(
    "SUPPORTBUNDLE_API_URL",
    "http://127.0.0.1:8000",
).rstrip("/")
REQUEST_TIMEOUT_SECONDS = 30.0
WORKFLOW_KEYS = ("workflow_id", "workflow_state", "bundle_bytes", "bundle_filename")
SCENARIO_DEFAULTS = {
    "application-startup-failure": {
        "title": "Application fails during startup",
        "application_name": "MapleDesk",
        "description": "Investigate a synthetic application startup failure.",
        "symptoms": "Application exits before becoming ready",
    },
    "high-cpu-usage": {
        "title": "Application is using high CPU",
        "application_name": "MapleDesk",
        "description": "Investigate sustained high CPU usage in a synthetic environment.",
        "symptoms": "High CPU usage and slow response",
    },
    "network-dependency-timeout": {
        "title": "Network dependency requests are timing out",
        "application_name": "MapleDesk",
        "description": "Investigate a synthetic timeout reaching an application dependency.",
        "symptoms": "Dependency requests time out",
    },
}

st.set_page_config(page_title="SupportBundleAI", page_icon="🛟", layout="centered")


def _safe_request_error(error: Exception) -> str:
    if isinstance(error, httpx.TimeoutException):
        return "The backend request timed out. Please try again."
    if isinstance(error, httpx.RequestError):
        return "The backend is unavailable. Check that FastAPI is running."
    return "The backend could not complete the request safely."


def _response_error(response: httpx.Response) -> str:
    messages = {
        403: "This action was blocked by the workflow safety or policy checks.",
        404: "The requested workflow, scenario, or bundle was not found.",
        409: "The workflow state changed or this approval is no longer pending. Refresh the workflow.",
        422: "Some request details are invalid. Review the fields and try again.",
        502: "The AI provider could not complete this workflow step.",
    }
    if response.status_code in messages:
        return messages[response.status_code]
    if response.status_code >= 500:
        return "The backend could not complete the request safely."
    return "The backend rejected the request."


def _request(method: str, path: str, **kwargs: Any) -> Any | None:
    try:
        response = httpx.request(
            method,
            f"{API_BASE_URL}{path}",
            timeout=REQUEST_TIMEOUT_SECONDS,
            **kwargs,
        )
    except httpx.RequestError as error:
        st.error(_safe_request_error(error))
        return None
    if not response.is_success:
        st.error(_response_error(response))
        return None
    return response


def _json_response(response: httpx.Response) -> dict[str, Any] | list[Any] | None:
    try:
        value = response.json()
    except ValueError:
        st.error("The backend returned an invalid response.")
        return None
    if not isinstance(value, (dict, list)):
        st.error("The backend returned an invalid response.")
        return None
    return value


def _store_workflow(response: httpx.Response) -> dict[str, Any]:
    state = _json_response(response)
    if not isinstance(state, dict):
        return {}
    st.session_state.workflow_id = state.get("workflow_id")
    st.session_state.workflow_state = state
    st.session_state.pop("bundle_bytes", None)
    st.session_state.pop("bundle_filename", None)
    return state


def _refresh_workflow() -> dict[str, Any] | None:
    workflow_id = st.session_state.get("workflow_id")
    if not workflow_id:
        return None
    response = _request("GET", f"/api/workflows/{workflow_id}")
    if response is None:
        return None
    return _store_workflow(response)


def _start_new_workflow() -> None:
    for key in WORKFLOW_KEYS:
        st.session_state.pop(key, None)
    st.rerun()


def _render_scenario_form(scenarios: list[dict[str, Any]]) -> None:
    scenario_by_id = {item["scenario_id"]: item for item in scenarios}
    scenario_ids = list(scenario_by_id)
    if not scenario_ids:
        st.warning("No supported synthetic scenarios are available.")
        return

    default_scenario = st.session_state.get("selected_scenario", scenario_ids[0])
    if default_scenario not in scenario_ids:
        default_scenario = scenario_ids[0]
    selected_id = st.selectbox(
        "Synthetic scenario",
        scenario_ids,
        index=scenario_ids.index(default_scenario),
        format_func=lambda scenario_id: scenario_by_id[scenario_id]["title"],
        key="selected_scenario",
    )
    selected_scenario = scenario_by_id[selected_id]
    defaults = SCENARIO_DEFAULTS.get(selected_id, {})
    st.caption(selected_scenario["description"])

    with st.form("start_workflow_form"):
        title = st.text_input("Issue title", value=defaults.get("title", selected_scenario["title"]))
        application = st.text_input(
            "Application name",
            value=defaults.get("application_name", "Demo application"),
        )
        description = st.text_area(
            "Issue description",
            value=defaults.get("description", selected_scenario["description"]),
        )
        symptoms = st.text_area(
            "Symptoms (one per line)",
            value=defaults.get("symptoms", ""),
        )
        submitted = st.form_submit_button("Start workflow", type="primary")

    if submitted:
        payload = {
            "title": title,
            "application_name": application,
            "description": description,
            "symptoms": [line.strip() for line in symptoms.splitlines() if line.strip()],
            "scenario_id": selected_id,
        }
        response = _request("POST", "/api/workflows", json=payload)
        if response is not None:
            state = _json_response(response)
            if not isinstance(state, dict) or not state.get("workflow_id"):
                st.error("The backend response did not include a workflow ID.")
                return
            st.session_state.workflow_id = state["workflow_id"]
            st.session_state.pop("bundle_bytes", None)
            st.session_state.pop("bundle_filename", None)
            _refresh_workflow()
            st.rerun()


def _render_plan_approval(state: dict[str, Any]) -> None:
    plan = state.get("proposed_plan")
    approval = state.get("pending_approval") or {}
    if not plan:
        st.error("The pending plan is unavailable. Refresh the workflow.")
        return
    st.subheader("Review proposed plan")
    if plan.get("policy_status"):
        st.write(f"Policy result: **{plan['policy_status']}**")
    eligible_steps: list[dict[str, Any]] = []
    for step in plan.get("steps", []):
        with st.container(border=True):
            st.markdown(f"**{step['collector']}**")
            st.write(step["reason"])
            st.write(f"Required: {'Yes' if step['required'] else 'No'}")
            st.write(f"Policy: {step.get('policy_status') or 'not available'}")
            if step.get("policy_reason"):
                st.caption(step["policy_reason"])
            st.json(step.get("parameters") or {})
        if step.get("policy_status") in {"approved", "approved_with_changes"}:
            eligible_steps.append(step)

    selected_ids = st.multiselect(
        "Steps to approve",
        [step["step_id"] for step in eligible_steps],
        default=[step["step_id"] for step in eligible_steps],
        format_func=lambda step_id: next(
            step["collector"] for step in eligible_steps if step["step_id"] == step_id
        ),
        key=f"plan_steps_{state['workflow_id']}",
    )
    workflow_id = state["workflow_id"]
    approval_id = approval.get("approval_id")
    all_ids = {step["step_id"] for step in eligible_steps}
    approve, reject = st.columns(2)
    with approve:
        if st.button(
            "Approve selected steps",
            type="primary",
            disabled=not selected_ids or not approval_id,
            key=f"approve_plan_{workflow_id}",
        ):
            rejected_ids = sorted(all_ids - set(selected_ids))
            decision = {
                "workflow_id": workflow_id,
                "approval_id": approval_id,
                "status": "approved" if not rejected_ids else "partially_approved",
                "approved_step_ids": selected_ids,
                "rejected_step_ids": rejected_ids,
            }
            response = _request(
                "POST",
                f"/api/workflows/{workflow_id}/approvals/plan",
                json=decision,
            )
            if response is not None:
                _store_workflow(response)
                st.rerun()
    with reject:
        if st.button("Reject plan", key=f"reject_plan_{workflow_id}", disabled=not approval_id):
            decision = {
                "workflow_id": workflow_id,
                "approval_id": approval_id,
                "status": "rejected",
                "approved_step_ids": [],
                "rejected_step_ids": [],
            }
            response = _request(
                "POST",
                f"/api/workflows/{workflow_id}/approvals/plan",
                json=decision,
            )
            if response is not None:
                _store_workflow(response)
                st.rerun()


def _render_evidence_approval(state: dict[str, Any]) -> None:
    workflow_id = state["workflow_id"]
    approval = state.get("pending_approval") or {}
    excerpts = state.get("evidence_review_excerpts", [])
    st.subheader("Review sanitized evidence")
    if state.get("warnings"):
        for warning in state["warnings"]:
            st.warning(warning)
    if not excerpts:
        st.info("No eligible sanitized excerpts are available for release.")
    choices: dict[str, str] = {}
    for excerpt in excerpts:
        with st.container(border=True):
            st.markdown(f"**Excerpt** `{excerpt['excerpt_id']}`")
            st.caption(f"Artifact `{excerpt['artifact_id']}`")
            st.write(excerpt["content"])
            st.write(
                f"Redactions: {excerpt['redaction_count']} · "
                f"Residual risk: {excerpt['residual_risk_level']}"
            )
            choices[excerpt["excerpt_id"]] = st.radio(
                "Decision",
                ["Include", "Exclude"],
                horizontal=True,
                key=f"excerpt_{workflow_id}_{excerpt['excerpt_id']}",
            )
    acknowledge = st.checkbox(
        "I reviewed the sanitized excerpts and acknowledge external AI analysis.",
        key=f"evidence_ack_{workflow_id}",
    )
    include_ids = {
        excerpt_id for excerpt_id, choice in choices.items() if choice == "Include"
    }
    by_artifact: dict[str, dict[str, list[str]]] = {}
    for excerpt in excerpts:
        decision = by_artifact.setdefault(
            excerpt["artifact_id"],
            {"approved_excerpt_ids": [], "excluded_excerpt_ids": []},
        )
        target = (
            "approved_excerpt_ids"
            if excerpt["excerpt_id"] in include_ids
            else "excluded_excerpt_ids"
        )
        decision[target].append(excerpt["excerpt_id"])
    items = []
    for artifact_id, excerpt_decisions in by_artifact.items():
        included = excerpt_decisions["approved_excerpt_ids"]
        items.append(
            {
                "artifact_id": artifact_id,
                "decision": "include" if included else "exclude",
                **excerpt_decisions,
            }
        )
    allow_analysis = bool(acknowledge and include_ids)
    submit, reject = st.columns(2)
    with submit:
        if st.button(
            "Approve selected evidence for analysis",
            type="primary",
            disabled=not allow_analysis or not approval.get("approval_id"),
            key=f"approve_evidence_{workflow_id}",
        ):
            decision = {
                "workflow_id": workflow_id,
                "approval_id": approval["approval_id"],
                "status": "approved",
                "item_decisions": items,
                "allow_external_ai_analysis": True,
                "reviewer_acknowledgement": True,
            }
            response = _request(
                "POST",
                f"/api/workflows/{workflow_id}/approvals/evidence",
                json=decision,
            )
            if response is not None:
                _store_workflow(response)
                st.rerun()
    with reject:
        if st.button(
            "Reject evidence release",
            disabled=not approval.get("approval_id"),
            key=f"reject_evidence_{workflow_id}",
        ):
            decision = {
                "workflow_id": workflow_id,
                "approval_id": approval["approval_id"],
                "status": "rejected",
                "item_decisions": [],
                "allow_external_ai_analysis": False,
                "reviewer_acknowledgement": bool(acknowledge),
            }
            response = _request(
                "POST",
                f"/api/workflows/{workflow_id}/approvals/evidence",
                json=decision,
            )
            if response is not None:
                _store_workflow(response)
                st.rerun()


def _render_analysis(analysis: dict[str, Any]) -> None:
    st.subheader("Analysis")
    st.write(f"**Issue summary:** {analysis['issue_summary']}")
    st.write(f"**Evidence sufficiency:** {analysis['evidence_sufficiency']}")
    for warning in analysis.get("warnings", []):
        st.warning(warning)
    for finding in analysis.get("findings", []):
        with st.expander(f"{finding['title']} · {finding['confidence']} confidence"):
            st.write(finding["description"])
            st.write(f"Category: {finding['category']}")
            st.write("Evidence references:")
            for reference in finding["evidence_references"]:
                st.write(
                    f"- Artifact `{reference['artifact_id']}`"
                    + (
                        f", excerpt `{reference['excerpt_id']}`"
                        if reference.get("excerpt_id") else ""
                    )
                )
            if finding.get("unverified_assumptions"):
                st.write("Assumptions:")
                for assumption in finding["unverified_assumptions"]:
                    st.write(f"- {assumption}")
            if finding.get("suggested_next_step"):
                st.write(f"Suggested next step: {finding['suggested_next_step']}")
    _render_missing_evidence(analysis.get("missing_evidence", []))


def _render_missing_evidence(items: list[dict[str, Any]]) -> None:
    if items:
        st.markdown("**Missing evidence**")
        for item in items:
            st.write(
                f"- {item['collector']}: {item['reason']} "
                f"(impact: {item['impact']})"
            )


def _render_bundle_approval(state: dict[str, Any]) -> None:
    review = state.get("bundle_review") or {}
    approval = state.get("pending_approval") or {}
    workflow_id = state["workflow_id"]
    st.subheader("Review verified bundle")
    st.write(f"ZIP filename: `{review.get('zip_filename', 'unavailable')}`")
    st.write(f"Manifest verification: **{review.get('manifest_verification', 'unknown')}**")
    st.write(f"Sanitized only: **{'Yes' if review.get('sanitized_only') else 'No'}**")
    st.write(f"Approved artifact count: {review.get('artifact_count', 0)}")
    for warning in review.get("warnings", []):
        st.warning(warning)
    _render_missing_evidence(review.get("missing_evidence", []))
    acknowledge = st.checkbox(
        "I reviewed this verified bundle and approve its release.",
        key=f"bundle_ack_{workflow_id}",
    )
    approve, reject = st.columns(2)
    with approve:
        if st.button(
            "Approve bundle release",
            type="primary",
            disabled=(
                not acknowledge
                or review.get("manifest_verification") != "verified"
                or not review.get("sanitized_only")
                or not approval.get("approval_id")
            ),
            key=f"approve_bundle_{workflow_id}",
        ):
            decision = {
                "workflow_id": workflow_id,
                "approval_id": approval["approval_id"],
                "bundle_id": review["bundle_id"],
                "status": "approved",
                "reviewer_acknowledgement": True,
            }
            response = _request(
                "POST",
                f"/api/workflows/{workflow_id}/approvals/bundle",
                json=decision,
            )
            if response is not None:
                _store_workflow(response)
                st.rerun()
    with reject:
        if st.button(
            "Reject bundle release",
            disabled=not approval.get("approval_id"),
            key=f"reject_bundle_{workflow_id}",
        ):
            decision = {
                "workflow_id": workflow_id,
                "approval_id": approval["approval_id"],
                "bundle_id": review["bundle_id"],
                "status": "rejected",
                "reviewer_acknowledgement": bool(acknowledge),
            }
            response = _request(
                "POST",
                f"/api/workflows/{workflow_id}/approvals/bundle",
                json=decision,
            )
            if response is not None:
                _store_workflow(response)
                st.rerun()


def _render_download(state: dict[str, Any]) -> None:
    workflow_id = state["workflow_id"]
    filename = state.get("bundle_filename") or "support-bundle.zip"
    if "bundle_bytes" not in st.session_state:
        if st.button("Prepare bundle download", key=f"prepare_download_{workflow_id}"):
            response = _request("GET", f"/api/workflows/{workflow_id}/bundle")
            if response is not None:
                st.session_state.bundle_bytes = response.content
                st.session_state.bundle_filename = filename
                st.rerun()
        return
    st.download_button(
        "Download verified bundle",
        data=st.session_state.bundle_bytes,
        file_name=st.session_state.get("bundle_filename", filename),
        mime="application/zip",
        key=f"download_bundle_{workflow_id}",
    )


def main() -> None:
    st.title("SupportBundleAI")
    st.caption(
        "Demo uses synthetic data, read-only collectors, local sanitization, "
        "and human approval."
    )

    workflow_id = st.session_state.get("workflow_id")
    if workflow_id:
        if st.button("Start new workflow", key="start_new_workflow"):
            _start_new_workflow()
        left, _right = st.columns([1, 4])
        with left:
            refresh = st.button("Refresh", key=f"refresh_{workflow_id}")
        if refresh:
            state = _refresh_workflow()
            if state is not None:
                st.rerun()
        state = st.session_state.get("workflow_state")
        if not state or state.get("workflow_id") != workflow_id:
            state = _refresh_workflow()
        if state is None:
            return
        st.subheader("Workflow status")
        st.write(f"**Status:** {state.get('status', 'unknown')}")
        st.write(f"**Current stage:** {state.get('current_stage', 'unknown')}")
        st.caption(f"Workflow ID: {workflow_id}")
        if state.get("pending_approval"):
            approval = state["pending_approval"]
            st.info(
                f"Pending {approval.get('approval_type', 'workflow')} approval: "
                f"{approval.get('title', 'Review required')}"
            )
        for warning in state.get("warnings", []):
            st.warning(warning)
        if state.get("error_message"):
            st.error(state["error_message"])
        if state.get("collection_progress"):
            st.markdown("**Collection progress**")
            for result in state["collection_progress"]:
                st.write(
                    f"- {result['collector']}: {result['status']}"
                    + (f" — {result['error_message']}" if result.get("error_message") else "")
                )
        sanitization = state.get("sanitization_summary")
        if sanitization:
            st.markdown("**Local sanitization**")
            st.write(
                f"{sanitization['status']} · "
                f"{sanitization['total_redactions']} redactions · "
                f"{sanitization['approved_artifact_count']} approved artifacts"
            )

        status_name = state.get("status")
        if status_name in {
            "awaiting_plan_approval",
            "awaiting_supplemental_approval",
        }:
            _render_plan_approval(state)
        elif status_name == "awaiting_evidence_approval":
            _render_evidence_approval(state)

        if state.get("analysis_result"):
            _render_analysis(state["analysis_result"])
        if status_name == "awaiting_release_approval":
            _render_bundle_approval(state)
        if state.get("bundle_available"):
            _render_download(state)
        return

    st.subheader("Start a diagnostic workflow")
    response = _request("GET", "/api/scenarios")
    if response is None:
        return
    scenarios = _json_response(response)
    if not isinstance(scenarios, list):
        st.error("The backend returned an invalid scenario response.")
        return
    _render_scenario_form(scenarios)


if __name__ == "__main__":
    main()