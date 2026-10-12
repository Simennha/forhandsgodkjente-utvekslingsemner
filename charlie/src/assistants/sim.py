"""The whole system wired in-process with simulators.

Used by the tests, ``make eval`` and as the reference wiring for the HTTP services.
Every security decision runs through the same classes as in production; only the outside
world (phones, providers, hardware, the LLM) is simulated.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from assistants.adapters.calendar import MockCalendar
from assistants.adapters.email import MockEmail
from assistants.adapters.hardware import SimCameraAudio, SimMicRelay
from assistants.adapters.homeassistant import MockHomeAssistant
from assistants.adapters.llm import LLMClient, MockLLM
from assistants.adapters.notifier import SimNotifier
from assistants.adapters.revocation import SimEgress, SimRevoker
from assistants.adapters.speech import SimSTT, SimTTS
from assistants.adapters.supplier import MockSupplierOrders
from assistants.adapters.telephony import SimTelephony
from assistants.agents.charlie import CharlieAgent
from assistants.agents.extractor import Extractor
from assistants.agents.lola import LolaAgent
from assistants.agents.mock_brains import mock_llm
from assistants.agents.report import make_report_writer
from assistants.approvals.service import (
    ApprovalRequest,
    ApprovalService,
    AttestationVerifier,
    Ed25519DeviceVerifier,
    approval_message,
)
from assistants.audit import MemoryAuditLog
from assistants.common.clock import FakeClock
from assistants.common.config import ROOT, AppConfig, load_config
from assistants.common.crypto import hash_credential
from assistants.events import MemoryEventQueue, Mode
from assistants.executor.core import CallContext, Executor, SubmitResult
from assistants.executor.handlers import ToolContext, ToolRunner
from assistants.executor.tokens import ActionTokens
from assistants.gateway.core import Gateway
from assistants.identity import Credential, Credentials, IdentityService
from assistants.inventory.repo import InventoryRepo, MemoryInventory
from assistants.killswitch.core import KSState
from assistants.killswitch.driver import (
    DeviceSecrets,
    KillSwitchDevice,
    MemoryDeviceState,
    ResumeAttempt,
    SimHAL,
    resume_message,
    rules_from_config,
)
from assistants.killswitch.lease import LeaseSigner, LeaseVerifier
from assistants.lola_queue.store import GuestInfo, MemoryGuestStore
from assistants.notes.store import MemoryNotes, MemoryProjects
from assistants.ops.costs import MeteredLLM
from assistants.policy import Policy, default_policy
from assistants.prompts import load_prompt
from assistants.rules.engine import Facts, evaluate
from assistants.storage.stores import (
    CostEntry,
    MemoryCostLedger,
    MemoryProposalStore,
    MemoryTokenLedger,
    MemoryToolCallLog,
)
from assistants.storage.transcripts import MemoryTranscripts
from assistants.workshop.mic import MicPipeline, SimWakeWord
from assistants.workshop.modes import MemoryModeStore, WorkshopModeController

DEMO_PIN = "482913"
DEMO_DURESS = "731864"
DEMO_TEST_WORD = "blåbær kompass"
DEMO_KS_SMS = "STOPP-SIMULERT-7Q"


def _credential(code: str, pepper: bytes = b"") -> Credential:
    salt = secrets.token_bytes(16)
    return Credential(salt, hash_credential(code, salt, pepper))


def seed_inventory() -> MemoryInventory:
    inv = MemoryInventory()
    inv.supplier_names = {
        "elfa": "Elfa Distrelec",
        "robotshop": "RobotShop",
        "conrad-ch": "Conrad CH",
    }
    for lok in ("NO", "CH"):
        for r in range(1, 12):
            for h in range(1, 4):
                inv.places[(lok, f"R{r}-H{h}")] = False
        inv.places[(lok, "ESD-1")] = True
    inv.articles = {
        "MOT-NEMA17": {
            "navn": "Steppermotor NEMA 17",
            "pris": 189.0,
            "leverandor": "robotshop",
            "esd": False,
        },
        "DRV-TMC2209": {
            "navn": "Motordriver TMC2209",
            "pris": 79.0,
            "leverandor": "elfa",
            "esd": True,
        },
        "MCU-ESP32S3": {
            "navn": "ESP32-S3 devkit",
            "pris": 149.0,
            "leverandor": "elfa",
            "esd": True,
        },
        "BAT-LIPO-4S": {
            "navn": "LiPo 4S 5000 mAh",
            "pris": 899.0,
            "leverandor": "robotshop",
            "esd": False,
        },
        "SRV-MG996R": {
            "navn": "Servo MG996R",
            "pris": 99.0,
            "leverandor": "conrad-ch",
            "esd": False,
        },
    }
    inv.stock_rows = {
        ("MOT-NEMA17", "NO", "R3-H2"): 12,
        ("DRV-TMC2209", "NO", "ESD-1"): 3,
        ("MCU-ESP32S3", "NO", "ESD-1"): 8,
        ("BAT-LIPO-4S", "NO", "R11-H1"): 2,
        ("SRV-MG996R", "CH", "R2-H1"): 4,
    }
    inv.rules = {
        ("MOT-NEMA17", "NO"): (6, 20),
        ("DRV-TMC2209", "NO"): (10, 30),
        ("BAT-LIPO-4S", "NO"): (4, 8),
        ("SRV-MG996R", "CH"): (6, 12),
    }
    return inv


def seed_guest() -> MemoryGuestStore:
    store = MemoryGuestStore()
    store.guest_info = [
        GuestInfo(
            "apningstider", "Åpningstider", "Åpent verksted torsdager 17-21 og lørdager 11-16."
        ),
        GuestInfo(
            "husregler",
            "Husregler",
            "Vernebriller ved alle maskiner. Ingen bruker maskiner alene. Rydd plassen din.",
        ),
        GuestInfo(
            "wifi",
            "Gjestenett",
            "Nettet heter Verksted-Gjest. Passordet står på oppslaget ved døra.",
        ),
        GuestInfo("adkomst", "Adkomst", "Inngang fra baksiden. Ring på dørtelefonen."),
    ]
    return store


@dataclass
class SimDevices:
    """Private keys of simulated owner devices (never present in production code paths)."""

    phone: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    box: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)
    second: Ed25519PrivateKey = field(default_factory=Ed25519PrivateKey.generate)

    def sign_approval(self, req: ApprovalRequest) -> bytes:
        key = self.box if req.method == "approval_box" else self.phone
        return key.sign(
            approval_message(req.proposal_id, req.params_hash, req.challenge, req.method, req.text)
        )

    def sign_resume(self, nonce: str, audit_head: str) -> bytes:
        return self.second.sign(resume_message(nonce, audit_head))


@dataclass
class SimKeyInventory:
    active: set[str] = field(
        default_factory=lambda: {
            "anthropic:key-charlie-1",
            "anthropic:key-lola-1",
            "twilio:key-1",
            "google:token-1",
            "ha:token-1",
        }
    )

    def active_key_fingerprints(self) -> frozenset[str]:
        return frozenset(self.active)


class SimSystem:
    def __init__(  # noqa: PLR0915 - explicit wiring reads best in one place
        self,
        cfg: AppConfig | None = None,
        llm: LLMClient | None = None,
        compromised: bool = False,
        policy: Policy | None = None,
        inventory: InventoryRepo | None = None,
    ) -> None:
        self.cfg = cfg or load_config(ROOT / "config.example.yaml")
        self.policy = policy or default_policy()
        self.clock = FakeClock()
        self.audit = MemoryAuditLog(self.clock)
        self.devices = SimDevices()

        # --- outside world (simulated)
        self.calendar = MockCalendar()
        self.email = MockEmail()
        allowed = frozenset(
            self.cfg.house.lights
            + self.cfg.house.climate
            + self.cfg.house.locks
            + self.cfg.house.workshop_lights
            + self.cfg.house.workshop_sensors
        )
        self.ha = MockHomeAssistant(allowed_entities=allowed)
        self.notifier = SimNotifier()
        self.telephony = SimTelephony(signing_key=secrets.token_urlsafe(16))
        self.supplier = MockSupplierOrders()
        self.stt = SimSTT()
        self.tts = SimTTS()
        self.relay = SimMicRelay()
        self.camera = SimCameraAudio()
        self.raw_llm: LLMClient = llm or mock_llm(compromised)

        # --- data
        self.inventory: InventoryRepo = inventory or seed_inventory()
        self.guest_store = seed_guest()
        self.notes = MemoryNotes()
        self.projects = MemoryProjects()
        self.transcripts = MemoryTranscripts()
        self.proposals = MemoryProposalStore()
        self.costs = MemoryCostLedger()
        self.calls = MemoryToolCallLog()
        self.lola_calls = MemoryToolCallLog()
        self.lola_costs = MemoryCostLedger()
        self.queue = MemoryEventQueue()

        # --- kill switch device (independent) and fail-closed lease views
        lease_key = Ed25519PrivateKey.generate()
        self.ks_executor_view = LeaseVerifier(lease_key.public_key(), self.clock)
        self.ks_lola_view = LeaseVerifier(lease_key.public_key(), self.clock)
        self.ks_hal = SimHAL()
        self.key_inventory = SimKeyInventory()
        self.revokers = [
            SimRevoker("anthropic"),
            SimRevoker("twilio"),
            SimRevoker("google"),
            SimRevoker("homeassistant"),
        ]
        self.egress = SimEgress()
        self.ks_notifier = SimNotifier()
        self.ks_state = MemoryDeviceState()
        self.lease_key = lease_key
        pin_salt, sms_salt = secrets.token_bytes(16), secrets.token_bytes(16)
        self.killswitch = KillSwitchDevice(
            hal=self.ks_hal,
            secrets=DeviceSecrets(
                pin_hash=hash_credential(DEMO_PIN, pin_salt),
                pin_salt=pin_salt,
                sms_code_hash=hash_credential(DEMO_KS_SMS, sms_salt),
                sms_code_salt=sms_salt,
                second_device_key=self.devices.second.public_key(),
            ),
            rules=rules_from_config(self.cfg.killswitch),
            clock=self.clock,
            signer=LeaseSigner(lease_key),
            lease_ttl_s=self.cfg.killswitch.lease_ttl_s,
            owner_numbers=frozenset(self.cfg.identity.owner_numbers),
            second_alarm_number=self.cfg.identity.second_alarm_number,
            known_numbers=frozenset(self.cfg.identity.known_contacts),
            revokers=self.revokers,
            egress=self.egress,
            notifier=self.ks_notifier,
            keys=self.key_inventory,
            audit=self.audit,
            publish_lease=self._on_lease,
            state_store=self.ks_state,
        )

        # --- approvals
        self.approvals = ApprovalService(
            signing_key=(appr_key := Ed25519PrivateKey.generate()),
            verifiers={
                "passkey": Ed25519DeviceVerifier(self.devices.phone.public_key()),
                "approval_box": Ed25519DeviceVerifier(self.devices.box.public_key()),
            },
            clock=self.clock,
        )

        # --- workshop
        self.mode_store = MemoryModeStore()
        self.workshop = WorkshopModeController(
            self.relay, self.camera, self.audit, store=self.mode_store
        )
        self.workshop.set_mode(Mode.NORMAL)  # the owner's first action after installation

        # --- executor (holds every key; here: the simulated adapters)
        self.charlie_llm = MeteredLLM(
            self.raw_llm, "charlie", self.cfg, self.costs, self.clock, on_call=self._log_llm
        )
        self.extractor = Extractor(
            self.charlie_llm, self.cfg, self.policy, load_prompt("extractor")
        )
        self.tool_ctx = ToolContext(
            cfg=self.cfg,
            clock=self.clock,
            calendar=self.calendar,
            email=self.email,
            notes=self.notes,
            projects=self.projects,
            inventory=self.inventory,
            ha=self.ha,
            notifier=self.notifier,
            telephony=self.telephony,
            supplier=self.supplier,
            transcripts=self.transcripts,
            extract=lambda src, text: self.extractor.extract(
                src, text, channel="email"
            ).model_dump(),
        )
        self.tool_ctx.report_writer = make_report_writer(self.charlie_llm, self.cfg)
        self.tokens = ActionTokens(
            secrets.token_bytes(32), self.clock, MemoryTokenLedger(), self.cfg.action_tokens.ttl_s
        )
        self.executor = Executor(
            policy=self.policy,
            cfg=self.cfg,
            clock=self.clock,
            rules=self.check_rules,
            killswitch=lambda: (self.ks_executor_view.state(), True),
            mode_view=self.workshop.mode,
            proposals=self.proposals,
            costs=self.costs,
            calls=self.calls,
            audit=self.audit,
            tokens=self.tokens,
            runner=ToolRunner(self.tool_ctx, self.tokens),
            inventory=self.inventory,
            request_approval=self.approvals.request,
            attestations=AttestationVerifier(appr_key.public_key(), self.clock),
            active_since=self.ks_executor_view.active_since,
        )

        # --- agents
        self.charlie = CharlieAgent(
            self.charlie_llm,
            self.extractor,
            self.executor.submit,
            self.cfg,
            self.policy,
            load_prompt("charlie"),
            self.clock,
        )
        self.lola_llm = MeteredLLM(
            self.raw_llm, "lola", self.cfg, self.lola_costs, self.clock, on_call=self._log_llm
        )
        self.lola = LolaAgent(
            self.lola_llm,
            self.guest_store.lola_view(),
            self.cfg,
            self.policy,
            load_prompt("lola"),
            self.check_rules,
            lambda: (self.ks_lola_view.state(), self.ks_lola_view.lola_may_act()),
            self.lola_calls,
            self.lola_costs,
            self.clock,
        )

        # --- identity + gateway
        pepper = secrets.token_bytes(32)
        self.identity = IdentityService(
            self.cfg.identity,
            self.cfg.rate_limits,
            Credentials(
                pepper,
                _credential(DEMO_PIN, pepper),
                _credential(DEMO_DURESS, pepper),
                _credential(DEMO_TEST_WORD, pepper),
            ),
            self.clock,
            on_duress=self.killswitch.duress,
        )
        self.gateway = Gateway(
            self.cfg,
            self.identity,
            self.queue,
            self.audit,
            self.clock,
            self.stt,
            self.tts,
            self.transcripts,
            self.workshop.mode,
            charlie=lambda text, ctx: self.charlie.handle_text(text, ctx).reply,
            lola=lambda text, ch, num: self.lola.handle(text, ch, num).reply,
            charlie_available=self.ks_executor_view.charlie_may_act,
        )
        self.wake = SimWakeWord(tuple(self.cfg.workshop.wake_words))
        self.mic = MicPipeline(
            self.workshop,
            self.wake,
            self.stt,
            self.tts,
            self.audit,
            charlie=lambda text, ctx: self.charlie.handle_text(text, ctx).reply,
            charlie_available=self.ks_executor_view.charlie_may_act,
            is_test_word=self.identity.is_test_word,
        )
        self.killswitch.boot(allow_first_boot=True)

    # ------------------------------------------------------------ wiring helpers
    def check_rules(self, facts: Facts) -> Any:
        return evaluate(facts, self.policy, self.cfg)

    def _log_llm(self, entry: CostEntry) -> None:
        self.audit.append(
            "ops",
            "llm.call",
            {
                "agent": entry.agent,
                "channel": entry.channel,
                "model": entry.model,
                "cost_nok": entry.cost_nok,
            },
        )

    def _on_lease(self, token: str) -> None:
        before = self.ks_executor_view.state()
        self.ks_executor_view.accept(token)
        self.ks_lola_view.accept(token)
        if before == KSState.ACTIVE and self.ks_executor_view.state() != KSState.ACTIVE:
            self.executor.freeze_all()

    def advance(self, seconds: float) -> None:
        """Advance simulated time; the kill-switch device keeps publishing leases meanwhile."""
        self.clock.advance(seconds)
        self.killswitch.publish()

    # ------------------------------------------------------------ owner devices
    def approve_on_phone(self, proposal_id: str) -> SubmitResult:
        req = self.approvals.pending(proposal_id)
        if req is None:
            raise LookupError("no pending approval")
        token = self.approvals.approve(proposal_id, req.method, self.devices.sign_approval(req))
        return self.executor.approve(token)

    def resume_killswitch(
        self,
        pin: str = DEMO_PIN,
        second_device: bool = True,
        reviewed: bool = True,
        keys_replaced: bool = True,
    ) -> KSState:
        if keys_replaced:
            self.key_inventory.active = {
                f"{k.split(':')[0]}:rotated-{secrets.token_hex(3)}"
                for k in self.key_inventory.active
            }
        nonce = self.killswitch.new_resume_nonce(secrets.token_urlsafe(12))
        head = self.audit.head_hash() if reviewed else "0" * 64
        sig = self.devices.sign_resume(nonce, head) if second_device else b"\x00" * 64
        return self.killswitch.resume(ResumeAttempt(pin, nonce, head, sig))

    def call(
        self,
        line: str,
        number: str,
        utterances: list[str],
        pin: str | None = None,
        consent: bool = True,
    ) -> list[str]:
        """Convenience: one whole phone call. Returns everything said by the system."""
        call_id = f"CA{secrets.token_hex(8)}"
        said = [self.gateway.call_started(line, call_id, number)]  # type: ignore[arg-type]
        if call_id in self.gateway.calls:
            said.append(self.gateway.consent(call_id, consent))
            if pin is not None:
                said.append(self.gateway.dtmf(call_id, pin))
            for u in utterances:
                said.append(self.gateway.audio(call_id, u.encode()))
            self.gateway.call_ended(call_id)
        return said

    def owner_ctx(self, trust: Any = None, mode: Mode = Mode.NORMAL) -> CallContext:
        from assistants.events import Trust  # noqa: PLC0415

        return CallContext(trust or Trust.OWNER_PIN, mode, "test")


def mock_of(system: SimSystem) -> MockLLM:
    if not isinstance(system.raw_llm, MockLLM):
        raise TypeError("system does not use the mock LLM")
    return system.raw_llm


__all__ = ["DEMO_DURESS", "DEMO_KS_SMS", "DEMO_PIN", "DEMO_TEST_WORD", "SimSystem", "mock_of"]
