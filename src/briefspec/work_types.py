from __future__ import annotations

import hashlib
import json
import math
import os
import re
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from functools import lru_cache
from importlib import resources
from typing import Any

from briefspec.models import (
    ClassificationConfidence,
    ClassificationOrigin,
    WorkType,
)

PROFILE_VERSION = "1.0"
ASSESSMENT_SECTION_ID = "assessment"
MAX_CLASSIFICATION_CHARS = 64 * 1024
# Rules read only the opening of a prompt: the request is near the start, and a bounded window
# keeps every pattern well inside host hook timeouts on adversarial input.
RULE_WINDOW_CHARS = 8 * 1024
CLASSIFIER_ADAPTER_VERSION = "1.3"
MIN_INFERRED_MARGIN = 1


@dataclass(frozen=True, slots=True)
class TypeSection:
    section_id: str
    label: str


@dataclass(frozen=True, slots=True)
class TypeProfile:
    work_type: str
    description: str
    sections: tuple[TypeSection, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "work_type": self.work_type,
            "profile_version": PROFILE_VERSION,
            "description": self.description,
            "sections": [asdict(section) for section in self.sections],
        }


@dataclass(frozen=True, slots=True)
class Classification:
    work_type: str
    subject: str
    confidence: str
    origin: str
    classified_at: str
    profile_version: str = PROFILE_VERSION
    rule_ids: tuple[str, ...] = ()
    decision_id: str = ""
    input_sha256: str = ""
    record_sha256: str = ""
    adapter_version: str = CLASSIFIER_ADAPTER_VERSION

    def to_dict(self) -> dict[str, Any]:
        value = asdict(self)
        value["rule_ids"] = list(self.rule_ids)
        return {key: item for key, item in value.items() if item != ""}


def _sections(*values: tuple[str, str]) -> tuple[TypeSection, ...]:
    return tuple(TypeSection(section_id, label) for section_id, label in values)


PROFILES: dict[str, TypeProfile] = {
    WorkType.GENERAL.value: TypeProfile(
        WorkType.GENERAL.value,
        "Give a direct answer, the reasoning needed to trust it, and the next useful move.",
        _sections(("answer", "Answer"), ("rationale", "Rationale"), ("next_action", "Next action")),
    ),
    WorkType.EXPLORATION.value: TypeProfile(
        WorkType.EXPLORATION.value,
        "Map an unfamiliar system without presenting investigation as implementation.",
        _sections(
            ("question", "Question"),
            ("system_map", "System map"),
            ("entry_points", "Entry points"),
            ("flow", "Flow"),
            ("unknowns", "Unknowns"),
            ("next_probe", "Next probe"),
        ),
    ),
    WorkType.REVIEW.value: TypeProfile(
        WorkType.REVIEW.value,
        "Lead with the verdict, then findings, risk, observed validation, and recommendation.",
        _sections(
            ("scope", "Scope"),
            ("verdict", "Verdict"),
            ("findings", "Findings"),
            ("risk", "Risk"),
            ("validation", "Validation"),
            ("recommendation", "Recommendation"),
        ),
    ),
    WorkType.IMPLEMENTATION.value: TypeProfile(
        WorkType.IMPLEMENTATION.value,
        "Explain what was intended, what changed, resulting behavior, and verification.",
        _sections(
            ("intent", "Intent"),
            ("changes", "Changes"),
            ("resulting_behavior", "Resulting behavior"),
            ("verification", "Verification"),
            ("tradeoffs", "Tradeoffs"),
        ),
    ),
    WorkType.DEBUGGING.value: TypeProfile(
        WorkType.DEBUGGING.value,
        "Separate the observed symptom from the proven cause, fix, and residual risk.",
        _sections(
            ("symptom", "Symptom"),
            ("root_cause", "Root cause"),
            ("fix", "Fix"),
            ("regression_protection", "Regression protection"),
            ("residual_risk", "Residual risk"),
        ),
    ),
    WorkType.PLANNING.value: TypeProfile(
        WorkType.PLANNING.value,
        "Turn intent into a decision-complete sequence with explicit release gates.",
        _sections(
            ("goal", "Goal"),
            ("decisions", "Decisions"),
            ("approach", "Approach"),
            ("sequence", "Sequence"),
            ("gates", "Gates"),
        ),
    ),
    WorkType.RESEARCH.value: TypeProfile(
        WorkType.RESEARCH.value,
        "Distinguish synthesis, evidence quality, current limits, and recommendation.",
        _sections(
            ("question", "Question"),
            ("synthesis", "Synthesis"),
            ("evidence_quality", "Evidence quality"),
            ("limitations", "Limitations"),
            ("recommendation", "Recommendation"),
        ),
    ),
    WorkType.OPERATIONS.value: TypeProfile(
        WorkType.OPERATIONS.value,
        "Make impact, current state, actions, recovery, and follow-up quickly scannable.",
        _sections(
            ("event", "Event"),
            ("impact", "Impact"),
            ("current_state", "Current state"),
            ("actions", "Actions"),
            ("recovery", "Recovery"),
            ("follow_up", "Follow-up"),
        ),
    ),
}

SUBJECTS = (
    "pull-request",
    "codebase",
    "change-set",
    "issue",
    "bug",
    "feature",
    "refactor",
    "test",
    "release",
    "architecture",
    "document",
    "data",
    "incident",
    "dependency",
    "security",
    "general",
)

# A verb that is also a common noun ("the new build", "a write path") is not a request.
_NOT_AFTER_DETERMINER = (
    r"(?<!\bthe\s)(?<!\ba\s)(?<!\ban\s)(?<!\bnew\s)(?<!\bthis\s)(?<!\bthat\s)"
    r"(?<!\bour\s)(?<!\byour\s)(?<!\bmy\s)(?<!\blatest\s)(?<!\blast\s)"
)

# Each rule is (rule_id, pattern, weight). Weight 2 marks an explicit request (usually the main
# verb); weight 1 marks supporting context such as a noun or a symptom. When two types tie, the
# type whose strongest rule matches earliest wins, because the main verb of an imperative
# request usually comes first ("review the plan" is review, "implement the plan" is
# implementation).
_CODE = r"(?:code|codebase|repo|module|file|function|class|cli|api|src/|\.py|\.js|\.ts|layer)"
_TYPE_RULES: dict[str, tuple[tuple[str, str, int], ...]] = {
    WorkType.GENERAL.value: (
        (
            "general.concept",
            r"\bwhat(?:'s| is) the difference between\b|\bdifference between\b.{0,120}\?"
            r"|\bconceptually\b|\bwhat(?:'s| is) an? \w+(?:[ -]\w+)? anyway\b"
            r"|\b(?:your|an) honest take\b|\bin (?:simple|plain) (?:terms|words)\b"
            r"|\bwhy do (?:people|we|developers|teams) (?:say|use|prefer)\b"
            r"|\bwhat(?:'s| is) the idea behind\b|\bis (?:it|this|that) (?:correct|true) that\b"
            r"|\bis (?:a|an) [\w -]{1,30} considered\b|\bqual a diferen[cç]a entre\b"
            r"|\banswer (?:what|why|how|whether|which|the question)\b"
            r"|\bo que [eé] (?:um|uma)\b",
            2,
        ),
        (
            "general.explain",
            r"^\s*(?:can you |please )?explain\b(?!\s+how\b[^.?!\n]{0,80}\b" + _CODE + r")"
            r"|^\s*(?:me )?explica\b(?![^.?!\n]{0,80}\bno c[oó]digo\b)",
            1,
        ),
    ),
    WorkType.DEBUGGING.value: (
        (
            "debug.explicit",
            r"\b(?:debug|diagnos(?:e|is)|root[- ]cause(?! analysis template)|troubleshoot|"
            r"track (?:it |this |that )?down|find out why|figure out why|help me find|"
            r"what'?s (?:going on|wrong)|what is (?:going on|wrong)|any idea why|"
            r"where is (?:this|that|it) coming from|investigate(?! the market)|"
            r"why (?:won't|doesn't|isn't|aren't|don't|can't|did)\b|dig deeper|"
            r"(?:know|tell me|understand) why\b|find out what\b|what'?s (?:looping|causing|"
            r"eating|hogging))",
            2,
        ),
        (
            "debug.failure",
            r"\b(?:failing|failure|fail(?:s|ed)?|broken|broke|crash(?:es|ed)?|exception|errors?|"
            r"hangs?|hanging|timeouts?|times out|timing out|oom|out of memory|deadlock|segfault|"
            r"flaky|regression|leak(?:s|ing)?|not (?:firing|working|running|loading)|"
            r"stopped working|permission denied|\d{3} errors?|\w+(?:Error|Exception)\b|"
            r"comes? out empty|exits? with code|pegged|still \d{3}|"
            r"traceback|stack trace|latency|slow(?:er)?)\b",
            1,
        ),
        (
            "debug.why",
            r"\bwhy\b[^.?!\n]{0,80}\b(?:slow|fail(?:s|ed|ing)?|broken|crash(?:es|ed)?|hangs?|"
            r"errors?|timing out|times out|twice|stale|missing|wrong|drops?|different|empty)\b",
            1,
        ),
        (
            "debug.explicit.pt",
            r"\b(?:depur(?:e|a|ar)|diagnostiqu?(?:e|ar)|causa raiz|investiga(?:r)?|"
            r"descobre por ?qu[eê]|o que (?:pode ser|est[aá] acontecendo)|me diz por ?qu[eê]|"
            r"qual (?:[eé] )?a causa)",
            2,
        ),
        (
            "debug.failure.pt",
            r"\b(?:falh(?:a|as|ando|ou)|quebrad[oa]s?|erros?|exce[cç][aã]o|trav(?:a|ando|ou)|"
            r"n[aã]o (?:est[aá] )?(?:funcionando|rodando))\b",
            1,
        ),
        ("debug.why.pt", r"\bpor ?que\b", 1),
    ),
    WorkType.REVIEW.value: (
        (
            "review.explicit",
            r"\b(?:review|audit|critique|inspect|look over|second opinion|sanity[- ]check|"
            r"give me a verdict|flag anything|list the problems|take a (?:critical )?(?:pass|look)|"
            r"does (?:this|it) look (?:right|ok|good)|anything off|skim|lgtm|rate this|"
            r"what'?s wrong with (?:this|my|the))\b",
            2,
        ),
        (
            "review.check",
            r"\b(?:check|go through|look at)\b[^.?!\n]{0,80}\b(?:for (?:issues|problems|bugs|"
            r"best[- ]practice|weak|anything)|is (?:it|this) (?:safe|correct|right|ok)|"
            r"if anything is (?:risky|wrong|off)|and (?:tell|flag|list))\b"
            r"|\bis (?:this|my|the) [\w ./-]{1,40} (?:safe|correct|right|clear|consistent|"
            r"clear enough|ok)\b",
            2,
        ),
        ("review.pr", r"\b(?:pull request|merge request|code review|prs?\s*#?\d+)\b", 1),
        (
            "review.diff",
            r"\b(?:diff|change set|changeset|commits?)\b.{0,120}"
            r"\b(?:risk|risky|quality|correct|issue)\b",
            1,
        ),
        (
            "review.explicit.pt",
            r"\brevis(?:e|ar)\s+(?:o|a|os|as|este|esta|esse|essa|meu|minha)\b|\brevisa\b"
            r"|\brevis[aã]o\b|\baudit(?:e|ar|oria)\b|\binspecion(?:e|ar)\b"
            r"|\bd[aá] uma (?:olhada|revisada)\b|\baponta (?:o que|os problemas)\b"
            r"|\baudit[ae]\b",
            2,
        ),
    ),
    WorkType.OPERATIONS.value: (
        ("operations.incident", r"\b(?:incident|outage|degradation|on-call|paged|sev[0-9])\b", 2),
        (
            "operations.act",
            rf"{_NOT_AFTER_DETERMINER}\b(?:re)?deploy\b(?! (?:logic|script|code|module|function))"
            r"|\bship (?:it|v?\d+\.\d+)"
            r"|\bupload [\w ]{0,40} to (?:test)?pypi\b|\bre-?trigger\b|\bfail ?over\b"
            r"|\broll ?back\b|\bpublish(?:es)?\b|\bpromote\b|\brestart\b|\breboot\b"
            r"|\bscale (?:up|down|out|in)?\b|\bdrain\b|\bcordon\b|\bfailover\b"
            r"|\brotate (?:the )?[\w ]{0,40}(?:credentials?|keys?|secrets?|tokens?|certs?)\b"
            r"|\brenew (?:the )?[\w .-]{0,40}certs?\b|\bflush (?:the )?[\w ]{0,40}cache\b"
            r"|\brun (?:the )?(?:database |db )?migrations?\b|\bapply the terraform\b"
            r"|\bprovision\b|\btag v?\d|\brerun (?:the )?(?:failed )?[\w ]{0,40}(?:job|workflow|"
            r"pipeline|build)\b|\b(?:disable|enable) (?:the )?feature flag\b"
            r"|\binstall [\w ./-]{0,40} on (?:the )?[\w-]*(?:host|server|box|cluster|node)\b"
            r"|\bmove the [\w ]{0,40}(?:node pool|cluster|instances?)\b",
            2,
        ),
        (
            "operations.status",
            r"\bis (?:ci|the (?:ci|pipeline|build)) green\b|\bci (?:is )?green\b"
            r"|\bcheck (?:if|whether) [\w ]{0,40}(?:job|backup|build|deploy|pipeline)\b"
            r"|\bwatch [\w ]{0,40}(?:logs?|error rate|metrics|dashboard)\b",
            2,
        ),
        (
            "operations.release",
            r"\b(?:deployment|rollout|release|production|prod|staging)\b",
            1,
        ),
        ("operations.observe", r"\b(?:monitor|alert|recovery|restore|uptime|5xx)\b", 1),
        (
            "operations.incident.pt",
            r"\b(?:incidente|indisponibilidade|fora do ar)\b",
            2,
        ),
        (
            "operations.act.pt",
            r"\b(?:publica|publique|reinicia|reinicie|rotaciona|rotacione|reverte|"
            r"sobe [\w ]{0,40}(?:pra|para) produ[cç][aã]o|faz (?:o )?deploy|implanta|"
            r"aumenta o n[uú]mero de r[eé]plicas)\b"
            r"|\bpipeline de ci est[aá] verde\b",
            2,
        ),
        (
            "operations.observe.pt",
            r"\b(?:monitor(?:e|ar)|alertas?|recupera[cç][aã]o|produ[cç][aã]o)\b",
            1,
        ),
    ),
    WorkType.RESEARCH.value: (
        (
            "research.explicit",
            r"\b(?:research|investigate the market|literature review|look (?:it |this )?up|"
            r"market scan|vendor scan|dig up|gather evidence|what(?:'s| is) new in|"
            r"state of the art|what are other (?:teams|projects|tools) using)\b"
            r"|\b(?:find|look for|search for|gather|collect|dig up)\b[^.?!\n]{0,80}\b(?:papers?|"
            r"articles?|benchmarks?|official docs|docs|documentation|examples?|stats|statistics|"
            r"evidence|discussions?|release notes|alternatives?|known issues|cves?|changelog|"
            r"postmortems?|write-?ups|case studies|prior art|reports)\b"
            r"|\bsearch around\b|\bprior art\b|\bwhat do (?:recent )?(?:papers|studies)\b"
            r"|\b(?:a|the) comparison of\b|\bactively maintained\b"
            r"|\bcompare\b[^.?!\n]{0,80}\b(?:vs\.?|versus|and)\b"
            r"|\bwhich [\w ]{0,40}(?:tools|libraries|crates|packages|platforms|databases|vendors|"
            r"services)\b[^.?!\n]{0,80}\b(?:support|use|are|offer)\b",
            2,
        ),
        (
            "research.current",
            r"\b(?:latest|current market|recent changes|pricing|licenses?|"
            r"recommended way|best practices?)\b",
            1,
        ),
        (
            "research.compare",
            r"\b(?:evaluate|benchmark|recommend)\b.{0,120}"
            r"\b(?:tools?|products?|vendors?|models?|libraries)\b",
            1,
        ),
        (
            "research.web",
            r"\b(?:browse|search the web|sources?|exa|tavily|firecrawl|pypi and github)\b",
            1,
        ),
        (
            "research.explicit.pt",
            r"\b(?:pesquis(?:e|ar|a))\b|\bprocur(?:a|e|ar) (?:artigos|benchmarks|exemplos|"
            r"estudos|documenta[cç][aã]o)\b|\bprocur(?:a|e|ar) a documenta[cç][aã]o\b"
            r"|\bcompar(?:a|e|ar) (?:os? )?(?:pre[cç]os|\w+ com)\b",
            2,
        ),
        (
            "research.current.pt",
            r"\b(?:mais recentes?|[uú]ltimas? novidades|estado da arte)\b",
            1,
        ),
        (
            "research.compare.pt",
            r"\b(?:compar(?:e|ar)|avali(?:e|ar))\b.{0,120}"
            r"\b(?:ferramentas?|produtos?|fornecedores?|modelos?)\b",
            1,
        ),
    ),
    WorkType.PLANNING.value: (
        ("planning.explicit", r"\b(?:plan|roadmap|strategy|proposal|implementation plan)\b", 2),
        (
            "planning.artifact",
            r"\b(?:create|write|draft|prepare|build|make)\s+(?:(?:a|an|the|our)\s+)?"
            r"(?:\w+\s+){0,2}(?:plan|roadmap|strategy|proposal|specification|spec|design doc)\b",
            2,
        ),
        (
            "planning.shape",
            r"\b(?:outline|lay out|think through|sequence (?:them|these|the)|"
            r"break (?:this|it|that|the|these)\b[^.?!\n]{0,80}\binto\b|"
            r"what(?:'s| is) the right order|how should we (?:structure|approach|split|organize|"
            r"version|divide|split)|propose|sketch|decompose|prioriti[sz]e|"
            r"what order should|what should go into)\b"
            r"|^\s*design (?:a|an|the)\b",
            2,
        ),
        ("planning.design", r"\b(?:design|architect|architecture|specification|spec)\b", 1),
        (
            "planning.sequence",
            r"\b(?:milestones?|phases?|acceptance criteria|release gates?|swimlanes?|"
            r"before (?:i|we) (?:code|build|start|move|implement))\b",
            1,
        ),
        (
            "planning.explicit.pt",
            r"\b(?:planej(?:e|a|ar|amento)|plano|roteiro|estrat[eé]gia|proposta|"
            r"desenha a arquitetura|quebra [\w ]{0,40} em tarefas|prop[oõ]e|deveria dividir)\b",
            2,
        ),
        (
            "planning.sequence.pt",
            r"\b(?:etapas?|fases?|marcos?|crit[eé]rios de aceita[cç][aã]o)\b",
            1,
        ),
    ),
    WorkType.EXPLORATION.value: (
        (
            "exploration.explicit",
            r"\b(?:explore|exploration|give me a tour|walk me through|"
            r"trace (?:how|the|where)|map (?:the|its|out|this)|what calls|call graph)\b"
            r"|\bshow me (?:how|where)\b"
            r"|\bwhere (?:is|are|do|does|did)\b[^.?!\n]{0,80}"
            r"\b(?:defined|called|raised|read|loaded|"
            r"live|lives|handled|created|configured|set)\b"
            r"|\bwhere(?:'s| is) [\w ./`'-]+ (?:defined|raised|called)\b"
            r"|\b(?:find|list) (?:every|all|each) (?:place|file|caller|usage)s?\b"
            r"|\bfind where\b"
            r"|\bwhich (?:files|modules|functions|tests|classes)\b[^.?!\n]{0,80}"
            r"\b(?:call|touch|cover|"
            r"depend|use|read|import)\b"
            r"|\bwhat (?:modules|files) (?:depend|import|use)\b"
            r"|\bhow (?:is|are|do|does)\b[^.?!\n]{0,80}\b(?:created|passed|flow|get|handled|wired|"
            r"handle|load|reach)\b[^.?!\n]{0,80}\b(?:code|layer|module|api|through|into|from)\b"
            r"|\b(?:what(?:'s| is) the )?structure of\b"
            r"|\bwhere (?:does|do) the\b|\bwho owns\b|\bfollow it back\b"
            r"|\bwhat (?:reads|writes|schedules|calls|uses|imports|triggers)\b"
            r"|\bshow me (?:every|all) (?:place|file|caller)s?\b",
            2,
        ),
        ("exploration.map", r"\b(?:map|trace|orient|understand|entry points?)\b", 1),
        (
            "exploration.codebase",
            r"\b(?:codebase|repository|repo)\b.{0,120}"
            r"\b(?:works?|structured|flow|entry point)\b",
            1,
        ),
        ("exploration.where", r"\b(?:where is|how does|explain how)\b", 1),
        (
            "exploration.explicit.pt",
            r"\b(?:explor(?:e|ar)|mape(?:ie|ia|ar))\b|\bmostra o caminho\b"
            r"|\bquais (?:arquivos|m[oó]dulos|fun[cç][oõ]es) (?:chamam|usam|dependem)\b"
            r"|\bcomo [\w ]{0,40} funciona no c[oó]digo\b|\bcomo o [\w-]+ chama\b"
            r"|\bquais testes exercitam\b|\bme mostra como\b|\bonde [\w ]{0,40} [eé] instanciad",
            2,
        ),
        (
            "exploration.where.pt",
            r"\b(?:onde fica|como funciona|me explique como)\b"
            r"|\breposit[oó]rio\b.{0,120}\b(?:funciona|estrutura|fluxo|pontos? de entrada)\b",
            1,
        ),
    ),
    WorkType.IMPLEMENTATION.value: (
        ("implementation.explicit", r"\b(?:implement|refactor)\b", 2),
        (
            "implementation.create",
            rf"{_NOT_AFTER_DETERMINER}\b(?:build|create|write|add|remove|extract|rename|"
            r"convert|delete|wire up|port|apply the fixes)\b"
            r"|\breplace [\w.`'-]+ with\b",
            2,
        ),
        (
            "implementation.make",
            r"^\s*(?:can you |please )?make (?:the|it|this|that|[\w-]+'s)\b"
            r"|^\s*(?:document|split|wire)\b"
            r"|\bi need (?:a|an) [\w -]{0,30}(?:endpoint|feature|command|option|flag|page|"
            r"button|export|field|setting)\b",
            2,
        ),
        (
            "implementation.change",
            r"\b(?:change|update|modify|patch|migrate|configure|install|upgrade|bump)\b",
            1,
        ),
        ("implementation.fix", rf"{_NOT_AFTER_DETERMINER}\bfix\b", 2),
        ("implementation.test", r"\b(?:add|write|implement)\b.{0,120}\btests?\b", 1),
        (
            "implementation.execute-plan",
            r"\b(?:go ahead|proceed|move forward|carry on|execute|carry out)\s+(?:with\s+)?"
            r"(?:the|this|that|our|your|my)\s+(?:\w+\s+)?plan\b",
            2,
        ),
        (
            "implementation.explicit.pt",
            r"\b(?:implement(?:e|a|ar)|constru(?:a|i|ir)|cri(?:e|a|ar)|escrev(?:a|e|er)|"
            r"adicion(?:e|a|ar)|remov(?:a|e|er)|refator(?:e|a|ar))\b",
            2,
        ),
        (
            "implementation.change.pt",
            r"\b(?:alter(?:e|a|ar)|atualiz(?:e|a|ar)|modifi(?:que|ca|car)|migr(?:e|a|ar)|"
            r"configur(?:e|a|ar)|instal(?:e|a|ar))\b",
            1,
        ),
        (
            "implementation.fix.pt",
            r"\b(?:corrij(?:a|am)|corrige|corrigir|consert(?:e|a|ar))\b",
            2,
        ),
        (
            "implementation.test.pt",
            r"\b(?:adicion(?:e|a|ar)|escrev(?:a|e|er)|implement(?:e|a|ar))\b.{0,120}\btestes?\b",
            1,
        ),
    ),
}

_SUBJECT_RULES: tuple[tuple[str, str, str], ...] = (
    (
        "pull-request",
        "subject.pull-request",
        r"\b(?:pull requests?|merge requests?|prs?\s*#?\d+)\b",
    ),
    (
        "codebase",
        "subject.codebase",
        r"\b(?:codebase|repository|repo|reposit[oó]rio|base de c[oó]digo)\b",
    ),
    ("change-set", "subject.change-set", r"\b(?:diffs?|change sets?|changesets?)\b"),
    (
        "incident",
        "subject.incident",
        r"\b(?:incidents?|outages?|sev[0-9]|degradation|incidentes?|fora do ar)\b",
    ),
    (
        "security",
        "subject.security",
        r"\b(?:security|vulnerabilit(?:y|ies)|cves?|threats?|seguran[cç]a|vulnerabilidades?)\b",
    ),
    (
        "dependency",
        "subject.dependency",
        r"\b(?:dependency|dependencies|package upgrades?|depend[eê]ncias?)\b",
    ),
    (
        "architecture",
        "subject.architecture",
        r"\b(?:architecture|architectural|system design|arquitetura)\b",
    ),
    (
        "release",
        "subject.release",
        r"\b(?:releases?|deploy(?:s|ments?)?|publish|rollouts?|lan[cç]amento|implanta[cç][aã]o)\b",
    ),
    ("refactor", "subject.refactor", r"\b(?:refactor(?:ing|s)?|refatora[cç][aã]o)\b"),
    (
        "test",
        "subject.test",
        r"\b(?:tests?|testing|pytest|unit tests?|integration tests?|testes?)\b",
    ),
    (
        "document",
        "subject.document",
        r"\b(?:documents?|documentation|readme|guides?|pdf|documenta[cç][aã]o|documentos?)\b",
    ),
    (
        "data",
        "subject.data",
        r"\b(?:data|database|schema|dataset|sql|dados|banco de dados)\b",
    ),
    (
        "bug",
        "subject.bug",
        r"\b(?:bugs?|defects?|broken|failures?|errors?|crash(?:es|ed)?|defeitos?|erros?|"
        r"quebrad[oa])\b",
    ),
    (
        "feature",
        "subject.feature",
        r"\b(?:features?|capabilit(?:y|ies)|funcionalidades?)\b",
    ),
    ("issue", "subject.issue", r"\bissues?\s*#?\d+\b"),
)

# When several subjects are mentioned, prefer the one that the selected work type is about:
# "Implement the login feature and write tests" is feature work that also mentions tests.
_SUBJECT_AFFINITY: dict[str, tuple[str, ...]] = {
    WorkType.REVIEW.value: ("pull-request", "change-set", "security", "codebase"),
    WorkType.DEBUGGING.value: ("bug", "incident", "test", "data", "dependency"),
    WorkType.IMPLEMENTATION.value: (
        "feature",
        "refactor",
        "bug",
        "document",
        "release",
        "dependency",
        "test",
    ),
    WorkType.OPERATIONS.value: ("incident", "release", "dependency"),
    WorkType.PLANNING.value: ("architecture", "release", "feature"),
    WorkType.RESEARCH.value: ("dependency", "security", "architecture", "feature"),
    WorkType.EXPLORATION.value: ("codebase", "architecture"),
}

_EXPLICIT_TYPE = re.compile(
    r"\b(?:brief-spec\s+)?(?:work\s+)?type\s*[:=]?\s*"
    r"(general|exploration|review|implementation|debugging|planning|research|operations)\b",
    re.IGNORECASE,
)
_PIVOT = re.compile(
    r"\b(?:new task|switch(?:ing)? to|instead(?:,|\s)|different task|now (?:please )?(?:review|"
    r"explore|implement|debug|plan|research|deploy)|nova tarefa|outra tarefa|mude para|"
    r"em vez disso)\b",
    re.IGNORECASE,
)
# A soft cue only signals a possible new task. The hook switches on it only when the new
# prompt independently classifies as a different, non-fallback work type.
_SOFT_PIVOT = re.compile(
    r"\b(?:now that|moving on|next up|next task|on to the next|another (?:task|thing|topic)|"
    r"new (?:topic|request|question)|separately|unrelated(?:ly)?|before we start|"
    r"let'?s (?:now )?(?:move|switch|turn|shift|focus)|agora que|mudando de assunto|"
    r"pr[oó]xima tarefa)\b",
    re.IGNORECASE,
)
# "Don't just review it, fix the bug": the first clause is what NOT to stop at, not the request.
_NOT_ONLY_SPAN = re.compile(
    r"\b(?:do\s+not|don't|n[aã]o)\s+(?:just|only|apenas|s[oó])\b[^,.;!?\n]{0,200}",
    re.IGNORECASE,
)
_NEGATED_SPAN = re.compile(
    r"\b(?:do\s+not|don't|never|avoid|without|must\s+not|should\s+not|"
    r"shouldn't|cannot|can't|n[aã]o|nunca|evite|sem)\b"
    r".*?(?=(?:[.;!?\n]|,\s*(?:just|only|apenas|s[oó])\b|"
    r"\b(?:but|however|instead|just|only|mas|por[eé]m|apenas)\b|$))",
    re.IGNORECASE | re.DOTALL,
)
# "Now that the audit is done, research X" describes finished context before the request.
_BACKGROUND_SPAN = re.compile(
    r"\b(?:now that|agora que)\b[^,.;!?\n]*[,.;]"
    r"|\b(?:before|after|once|until|depois que|antes de)\s+(?:we|i|you|it|the|this|that|our|"
    r"a|o|a|n[oó]s)\b[^,.;!?\n]*",
    re.IGNORECASE,
)
_PATH_SPAN = re.compile(
    r"(?<![\w@])(?:[\w.-]{1,64}/){1,12}[\w.-]{0,64}|\b[\w-]{1,64}\.(?:py|js|ts|tsx|md|sql|json|ya?ml|toml|sh|tf|go|"
    r"rs|java|rb|txt|cfg|ini|lock)\b"
)
_QUOTED_SPAN = re.compile(r"(?<!\w)'.*?'(?!\w)|\".*?\"|`.*?`", re.DOTALL)
# Product names are not task vocabulary: "task-spec" must not read as a planning "spec".
_BRAND_SPAN = re.compile(
    r"\b(?:brief|task|keep)-?spec\b|\b(?:seamwise|workhelm|taskmesh)\b",
    re.IGNORECASE,
)


def normalize_subject(value: str | None) -> str:
    if not value:
        return "general"
    normalized = re.sub(r"[^a-z0-9]+", "-", value.strip().lower()).strip("-")
    if not normalized or len(normalized) > 64:
        raise ValueError("Subject must normalize to a non-empty slug of at most 64 characters")
    return normalized


def _timestamp(now: datetime | None) -> str:
    if now is None and (epoch := os.environ.get("SOURCE_DATE_EPOCH")):
        now = datetime.fromtimestamp(int(epoch), tz=UTC)
    now = now or datetime.now(UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now.astimezone(UTC).isoformat().replace("+00:00", "Z")


def is_clear_pivot(text: str) -> bool:
    return bool(_PIVOT.search(_affirmative_text(text[:MAX_CLASSIFICATION_CHARS])))


def is_soft_pivot(text: str) -> bool:
    """Return whether the prompt carries a weak new-task cue such as "now that" or "moving on"."""
    return bool(
        _SOFT_PIVOT.search(
            _affirmative_text(text[:MAX_CLASSIFICATION_CHARS], mask_background=False)
        )
    )


# Question-shaped rules ("is this correct?", "is ci green?") describe the current work; they
# do not ask for a different kind of work, so they never switch a sticky type.
_QUESTION_RULES = frozenset({"review.check", "operations.status"})
_DECISIVE_RULES = frozenset(
    rule_id
    for rules in _TYPE_RULES.values()
    for rule_id, _, weight in rules
    if weight >= 2 and rule_id not in _QUESTION_RULES
)
_RULE_PATTERNS = {
    rule_id: pattern for rules in _TYPE_RULES.values() for rule_id, pattern, _ in rules
}
_CLAUSE_LEAD = re.compile(
    r"(?:^|[.;:!?\n,]|\b(?:please|now|also|then|and|so|ok|okay|pls|can you|could you|"
    r"would you|let'?s|go ahead and|i want you to|i need you to|por favor|agora|tamb[eé]m|"
    r"e|ent[aã]o|pode|voc[eê] pode))\s*$",
    re.IGNORECASE,
)


def _starts_clause(text: str, position: int) -> bool:
    return bool(_CLAUSE_LEAD.search(text[max(0, position - 40) : position]))


def is_decisive_shift(
    candidate: Classification, current_type: str | None, text: str | None = None
) -> bool:
    """Whether a new prompt should replace a sticky type without an explicit cue.

    The candidate must name a different type through an explicit request verb (a weight-2
    rule), so follow-ups such as "go ahead" or "also check X" keep the current type.
    """
    if candidate.origin == ClassificationOrigin.FALLBACK.value:
        return False
    if candidate.work_type == current_type:
        return False
    if text is None:
        return any(rule in _DECISIVE_RULES for rule in candidate.rule_ids)
    affirmative = _affirmative_text(text)
    for rule in candidate.rule_ids:
        if rule not in _DECISIVE_RULES:
            continue
        for match in re.finditer(_RULE_PATTERNS[rule], affirmative, re.I | re.M):
            if _starts_clause(affirmative, match.start()):
                return True
    return False


def explicit_type_requested(text: str) -> bool:
    return bool(_EXPLICIT_TYPE.search(_affirmative_text(text[:MAX_CLASSIFICATION_CHARS])))


def is_substantive(text: str) -> bool:
    value = text[:MAX_CLASSIFICATION_CHARS].strip()
    if not value:
        return False
    if re.search(r"\bbrief-spec\b", value, re.IGNORECASE):
        return True
    if len(value.split()) < 4:
        return False
    affirmative = _affirmative_text(value)
    return any(
        re.search(pattern, affirmative, re.IGNORECASE)
        for rules in _TYPE_RULES.values()
        for _, pattern, _ in rules
    )


def _affirmative_text(text: str, *, mask_background: bool = True) -> str:
    """Mask bounded prohibitions, quoted examples, and finished-context clauses."""

    def mask(match: re.Match[str]) -> str:
        return " " * len(match.group(0))

    value = _BRAND_SPAN.sub(mask, text[:RULE_WINDOW_CHARS])
    value = _QUOTED_SPAN.sub(lambda match: " " * len(match.group(0)), value)
    value = _NOT_ONLY_SPAN.sub(mask, value)
    value = _PATH_SPAN.sub(lambda match: " file ", value)
    value = _NEGATED_SPAN.sub(mask, value)
    return _BACKGROUND_SPAN.sub(mask, value) if mask_background else value


def _select_subject(work_type: str, affirmative: str) -> tuple[str, str] | None:
    matched = [
        (candidate, rule_id)
        for candidate, rule_id, pattern in _SUBJECT_RULES
        if re.search(pattern, affirmative, re.IGNORECASE)
    ]
    if not matched:
        return None
    by_subject = dict(matched)
    for preferred in _SUBJECT_AFFINITY.get(work_type, ()):
        if preferred in by_subject:
            return preferred, by_subject[preferred]
    return matched[0]


def _finalize_classification(
    *,
    work_type: str,
    subject: str,
    confidence: str,
    origin: str,
    classified_at: str,
    rule_ids: tuple[str, ...],
    bounded: str,
) -> Classification:
    input_sha256 = hashlib.sha256(bounded.encode("utf-8")).hexdigest()
    record = {
        "adapter_version": CLASSIFIER_ADAPTER_VERSION,
        "classified_at": classified_at,
        "confidence": confidence,
        "input_sha256": input_sha256,
        "origin": origin,
        "profile_version": PROFILE_VERSION,
        "rule_ids": list(rule_ids),
        "subject": subject,
        "work_type": work_type,
    }
    record_sha256 = hashlib.sha256(
        json.dumps(record, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return Classification(
        work_type=work_type,
        subject=subject,
        confidence=confidence,
        origin=origin,
        classified_at=classified_at,
        rule_ids=rule_ids,
        decision_id=f"bsd-{record_sha256[:24]}",
        input_sha256=input_sha256,
        record_sha256=record_sha256,
    )


def classify_task(
    text: str,
    *,
    explicit_type: str | None = None,
    subject: str | None = None,
    host_context: dict[str, Any] | None = None,
    default_type: str = WorkType.GENERAL.value,
    now: datetime | None = None,
) -> Classification:
    bounded = text[:MAX_CLASSIFICATION_CHARS]
    affirmative = _affirmative_text(bounded)
    host_context = host_context or {}
    explicit_match = _EXPLICIT_TYPE.search(affirmative)
    requested_type = explicit_type or (explicit_match.group(1) if explicit_match else None)
    host_subject_hint: str | None = None
    if requested_type:
        work_type = WorkType(requested_type.lower()).value
        origin = ClassificationOrigin.EXPLICIT.value
        confidence = ClassificationConfidence.HIGH.value
        rule_ids = ("explicit.work-type",)
    else:
        host_type = str(host_context.get("work_type") or "").lower()
        if host_type not in PROFILES and any(
            host_context.get(key)
            for key in ("pull_request", "pull_request_url", "pr_number", "review_command")
        ):
            host_type = WorkType.REVIEW.value
            host_subject_hint = "pull-request"
        if host_type in PROFILES:
            work_type = host_type
            origin = ClassificationOrigin.HOST.value
            confidence = ClassificationConfidence.HIGH.value
            rule_ids = ("host.work-type",)
        else:
            matches: dict[str, list[str]] = {}
            scores: dict[str, int] = {}
            first_strong: dict[str, int] = {}
            for candidate, rules in _TYPE_RULES.items():
                found: list[tuple[str, int, int]] = []
                for rule_id, pattern, weight in rules:
                    hit = re.search(pattern, affirmative, re.I | re.M)
                    if hit:
                        found.append((rule_id, weight, hit.start()))
                if found:
                    matches[candidate] = [rule_id for rule_id, _, _ in found]
                    scores[candidate] = sum(weight for _, weight, _ in found)
                    strongest = max(weight for _, weight, _ in found)
                    first_strong[candidate] = min(
                        start for _, weight, start in found if weight == strongest
                    )
            if not matches:
                work_type = WorkType(default_type).value
                origin = ClassificationOrigin.FALLBACK.value
                confidence = ClassificationConfidence.LOW.value
                rule_ids = ("fallback.general",)
            else:
                top_score = max(scores.values())
                winners = [candidate for candidate, score in scores.items() if score == top_score]
                if len(winners) > 1 and top_score >= 2:
                    # The main verb of an imperative request usually comes first.
                    earliest = min(first_strong[candidate] for candidate in winners)
                    leaders = [c for c in winners if first_strong[c] == earliest]
                    if len(leaders) == 1:
                        winners = leaders
                runner_up = max(
                    (score for candidate, score in scores.items() if candidate not in winners),
                    default=0,
                )
                tie_broken = len(winners) == 1 and top_score == runner_up
                if len(winners) != 1 or (
                    top_score - runner_up < MIN_INFERRED_MARGIN and not tie_broken
                ):
                    work_type = WorkType(default_type).value
                    origin = ClassificationOrigin.FALLBACK.value
                    confidence = ClassificationConfidence.LOW.value
                    rule_ids = tuple(
                        sorted(rule for candidate in winners for rule in matches[candidate])
                    )
                else:
                    work_type = winners[0]
                    origin = ClassificationOrigin.INFERRED.value
                    confidence = ClassificationConfidence.MEDIUM.value
                    rule_ids = tuple(matches[work_type])

    if origin in {
        ClassificationOrigin.FALLBACK.value,
        ClassificationOrigin.INFERRED.value,
    } and not any(rule in _DECISIVE_RULES for rule in rule_ids):
        predicted = model_prediction(bounded)
        if (
            predicted is not None
            and predicted[1] >= _MODEL_MIN_PROBABILITY
            and predicted[0] != work_type
        ):
            work_type = predicted[0]
            if work_type == WorkType.GENERAL.value:
                origin = ClassificationOrigin.FALLBACK.value
                confidence = ClassificationConfidence.LOW.value
                rule_ids = ("model.naive-bayes",)
            else:
                origin = ClassificationOrigin.INFERRED.value
                confidence = (
                    ClassificationConfidence.MEDIUM.value
                    if predicted[1] >= 0.8
                    else ClassificationConfidence.LOW.value
                )
                rule_ids = ("model.naive-bayes",)

    host_subject = str(host_context.get("subject") or "") or host_subject_hint
    resolved_subject = subject or host_subject
    subject_rule: str | None = None
    if resolved_subject is None and origin != ClassificationOrigin.FALLBACK.value:
        selected = _select_subject(work_type, affirmative)
        if selected is not None:
            resolved_subject, subject_rule = selected
    normalized_subject = normalize_subject(resolved_subject)
    if subject_rule:
        rule_ids = (*rule_ids, subject_rule)
    elif subject or host_subject:
        rule_ids = (*rule_ids, "explicit.subject" if subject else "host.subject")

    deduplicated_rules = tuple(dict.fromkeys(rule_ids))
    classified_at = _timestamp(now)
    return _finalize_classification(
        work_type=work_type,
        subject=normalized_subject,
        confidence=confidence,
        origin=origin,
        classified_at=classified_at,
        rule_ids=deduplicated_rules,
        bounded=bounded,
    )


_MODEL_MIN_PROBABILITY = 0.6
MODEL_MAX_WORDS = 60
_TOKEN = re.compile(r"[a-z0-9\u00c0-\u024f']+")


def model_features(text: str) -> list[str]:
    """Word, bigram, and first-word features over the same masked text the rules see."""
    # The request is at the start; long tails of output instructions would only add noise and
    # make the summed log-probabilities overconfident.
    words = _TOKEN.findall(_affirmative_text(text[:MAX_CLASSIFICATION_CHARS]).lower())[
        :MODEL_MAX_WORDS
    ]
    features = [f"w:{word}" for word in words]
    features.extend(f"b:{first}_{second}" for first, second in zip(words, words[1:], strict=False))
    if words:
        features.append(f"first:{words[0]}")
    return features


@lru_cache(maxsize=1)
def _model() -> dict[str, Any] | None:
    try:
        # importlib.resources also reads from the zipapp that host hooks run.
        resource = resources.files("briefspec").joinpath("data", "classifier-model.json")
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return value if value.get("kind") == "brief-spec-classifier-model" else None


def model_prediction(text: str) -> tuple[str, float] | None:
    """Return (work type, probability) from the shipped Naive Bayes model, if available."""
    model = _model()
    if model is None:
        return None
    features = model_features(text)
    scores: dict[str, float] = {}
    for label, prior in model["priors"].items():
        weights = model["weights"][label]
        unseen = model["unseen"][label]
        score = prior
        for feature in features:
            if any(feature in model["weights"][other] for other in model["weights"]):
                score += weights.get(feature, unseen)
        scores[label] = score
    best = max(scores, key=lambda label: (scores[label], label))
    peak = scores[best]
    total = sum(math.exp(score - peak) for score in scores.values())
    return best, 1 / total


def type_profile(work_type: str) -> TypeProfile:
    try:
        return PROFILES[WorkType(work_type).value]
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Unknown Brief-Spec work type: {work_type}") from exc


def types_document() -> dict[str, Any]:
    return {
        "profile_version": PROFILE_VERSION,
        "types": [PROFILES[item.value].to_dict() for item in WorkType],
        "subjects": list(SUBJECTS),
        "custom_primary_types": False,
    }


def validate_explanation(
    classification: dict[str, Any], explanation: dict[str, Any]
) -> tuple[str, ...]:
    errors: list[str] = []
    try:
        profile = type_profile(str(classification.get("work_type", "")))
    except ValueError as exc:
        return (str(exc),)
    if explanation.get("profile_version") != PROFILE_VERSION:
        errors.append(f"Explanation profile_version must be {PROFILE_VERSION}")
    sections = explanation.get("sections")
    if not isinstance(sections, list):
        return (*errors, "Explanation sections must be an array")
    expected = [section.section_id for section in profile.sections]
    observed: list[str] = []
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"explanation.sections[{index}] must be an object")
            continue
        section_id = str(section.get("id", ""))
        observed.append(section_id)
        if not str(section.get("label", "")).strip():
            errors.append(f"explanation.sections[{index}].label is required")
        if not str(section.get("content", "")).strip():
            errors.append(f"explanation.sections[{index}].content is required")
    # One optional trailing Assessment keeps the agent's interpretation apart from the
    # facts in Proof (SBAR); every other section must follow the profile exactly.
    if observed != expected and observed != [*expected, ASSESSMENT_SECTION_ID]:
        errors.append("Explanation sections do not match the selected type profile order")
    return tuple(errors)
