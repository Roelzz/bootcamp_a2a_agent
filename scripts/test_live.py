#!/usr/bin/env python3
import argparse
import os
import uuid

import httpx


def send_message(client: httpx.Client, url: str, text: str) -> str:
    response = client.post(
        url,
        json={
            "jsonrpc": "2.0",
            "id": str(uuid.uuid4()),
            "method": "message/send",
            "params": {
                "message": {
                    "kind": "message",
                    "messageId": str(uuid.uuid4()),
                    "contextId": str(uuid.uuid4()),
                    "role": "user",
                    "parts": [{"kind": "text", "text": text}],
                }
            },
        },
    )
    response.raise_for_status()
    body = response.json()
    if "error" in body:
        raise RuntimeError(f"A2A request failed: {body['error'].get('message', 'unknown error')}")
    result = body.get("result")
    if not isinstance(result, dict):
        raise RuntimeError("A2A response did not contain a result message")
    return "".join(part["text"] for part in result.get("parts", []) if part.get("kind") == "text")


def assert_status(label: str, response: str, expected: str) -> None:
    if f"status={expected}" not in response:
        raise AssertionError(f"{label} expected status={expected}, received: {response}")
    print(f"{label}: status={expected}")


def main() -> None:
    parser = argparse.ArgumentParser(description="Smoke-test the live LiftLine A2A agent.")
    parser.add_argument(
        "--base-url",
        default=os.getenv("A2A_E2E_URL"),
        help="Public app base URL; defaults to A2A_E2E_URL.",
    )
    args = parser.parse_args()
    if not args.base_url:
        parser.error("provide --base-url or set A2A_E2E_URL")
    base_url = args.base_url.rstrip("/")
    card_path = "/.well-known/agent-card.json"

    with httpx.Client(timeout=90) as client:
        card_response = client.get(f"{base_url}{card_path}")
        card_response.raise_for_status()
        card = card_response.json()
        if card.get("name") != "LiftLine Dispatch Advisor":
            raise AssertionError(f"Unexpected agent name: {card.get('name')!r}")
        if card.get("protocolVersion") != "0.3.0":
            raise AssertionError("Agent card does not advertise A2A 0.3.0")
        if not any(skill.get("id") == "liftline-dispatch" for skill in card.get("skills", [])):
            raise AssertionError("Agent card does not advertise the LiftLine dispatch Skill")
        endpoint = card.get("url")
        if not isinstance(endpoint, str) or not endpoint.startswith("https://"):
            raise AssertionError("Agent card does not advertise a public HTTPS message endpoint")
        if endpoint != f"{base_url}/":
            raise AssertionError("Agent card message URL does not match the live app base URL")
        print(f"Agent card: {card['name']} ({card['protocolVersion']})")

        baseline = (
            "Evaluate delivery from Zava Regional depot (ZAVA-DEPOT) to Riverside renovation "
            "site on 2030-07-14, in Europe/Amsterdam, before 10:00. Shipment: 60 whole bags "
            "of repair mortar MORTAR-20, 20 kg each. There is no forklift at the destination. "
            "Use the synthetic baseline handling assumptions. Return eligible options, price, "
            "loaded weight, arrival window, exclusions, and assumptions. Do not book anything."
        )
        baseline_reply = send_message(client, f"{base_url}{card_path}", baseline)
        assert_status("Baseline through advertised card path", baseline_reply, "ok")
        for expected in ("TAIL-LIFT — EUR 180", "Loaded weight: 1300 kg", "LIFTLINE-DEMO-v1"):
            if expected not in baseline_reply:
                raise AssertionError(f"Baseline reply missing {expected!r}: {baseline_reply}")

        clarification = (
            "Evaluate delivery from ZAVA-DEPOT to Riverside renovation site on 2030-07-14 "
            "before 10:00. Shipment: 60 bags of MORTAR-20, 20 kg each. No forklift."
        )
        clarification_reply = send_message(client, endpoint, clarification)
        assert_status(
            "Clarification through card-advertised endpoint",
            clarification_reply,
            "needs_information",
        )
        if "timezone" not in clarification_reply.casefold():
            raise AssertionError("Clarification did not ask for the missing timezone")

        overloaded = (
            "Evaluate delivery from ZAVA-DEPOT to Riverside renovation site on 2030-07-14, "
            "Europe/Amsterdam, before 10:00. Shipment: 100 bags of MORTAR-20, 20 kg each, "
            "loaded weight 2100 kg. No forklift. Suitable vehicle access, level hardstanding, "
            "compatible pallet, and ground-level unloading are confirmed."
        )
        overloaded_reply = send_message(client, f"{base_url}{card_path}", overloaded)
        assert_status(
            "Over-capacity request through card path", overloaded_reply, "no_eligible_service"
        )
        if "exceeds 1500 kg" not in overloaded_reply:
            raise AssertionError("Over-capacity reply did not explain the service limits")

    print("Live LiftLine smoke test passed.")


if __name__ == "__main__":
    main()
