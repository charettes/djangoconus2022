import csv
import pickle
import re
import sys
from collections import Counter, defaultdict
from enum import Enum
from io import StringIO
from typing import NamedTuple

import git
import requests

DJANGO_REPO_DIR = "~/Workspace/django"

repo = git.Git(DJANGO_REPO_DIR)

contributions_path = "django/db/models"

backport_re = re.compile("^\[\d+\.\d+\.x]")
fixed_re = re.compile("Fixed #(\d+)", re.IGNORECASE)
releases_tag = [
    tag for tag in repo.tag("-l").split("\n") if re.match(r"^\d\.\d+$", tag)
]
releases_tag.sort(key=lambda tag: tuple(map(int, tag.split("."))))


class Release(NamedTuple):
    release: str
    contributions: int
    contributors: int
    new_contributors: int
    over_2_contributions: int
    over_10_contributions: int
    bugfixes: int
    optimizations: int
    features: int


class ContributionType(Enum):
    BUGFIX = "bugfix"
    FEATURE = "feature"
    OPTIMIZATION = "optimization"
    UNCATEGORIZED = "uncategorized"

    @classmethod
    def from_trac(cls, trac_type: str) -> "ContributionType":
        return {
            "Bug": cls.BUGFIX,
            "": cls.UNCATEGORIZED,
            "New feature": cls.FEATURE,
            "defect": cls.BUGFIX,
            "enhancement": cls.OPTIMIZATION,
            "Uncategorized": cls.UNCATEGORIZED,
            "Cleanup/optimization": cls.OPTIMIZATION,
        }[trac_type]


class Contribution(NamedTuple):
    type: ContributionType
    backport: bool
    release_blocker: bool

    @classmethod
    def from_trac(cls, ticket: str, backport: bool) -> "Contribution":
        response = requests.post(
            "https://code.djangoproject.com/jsonrpc",
            json={"method": "ticket.get", "params": [ticket]},
        )
        response.raise_for_status()
        record = response.json()["result"][-1]
        return cls(
            ContributionType.from_trac(record["type"]),
            backport,
            record["severity"] == "Release blocker",
        )


class Contributions(defaultdict):
    def __init__(self, default=dict):
        super().__init__(default)

    _cache_path = "contributions.pickled"

    def count_type(self, release: str, contribution_type: ContributionType):
        return sum(
            1 for cont in self[release].values() if cont.type == contribution_type
        )

    @classmethod
    def from_cache(cls):
        try:
            with open(cls._cache_path, "rb") as file:
                return pickle.load(file)
        except FileNotFoundError:
            return cls()

    def cache(self):
        with open(self._cache_path, "wb+") as file:
            pickle.dump(self, file)


releases = []
all_contributors = set()
contributions = Contributions.from_cache()
for release, next_release in zip(releases_tag[4:], releases_tag[5:] + ["main"]):
    counts = Counter(
        {
            contributor: int(count.strip())
            for count, contributor in csv.reader(
                StringIO(
                    repo.shortlog(
                        "-sn", f"{release}..{next_release}", "--", contributions_path
                    )
                ),
                dialect="excel-tab",
            )
        }
    )
    if not all_contributors:
        new_contributors = 0
    else:
        new_contributors = len(set(counts) - all_contributors)
    all_contributors.update(counts)
    for line in repo.log(
        "--format=%s", f"{release}..{next_release}", "--", contributions_path
    ).splitlines():
        fixed = fixed_re.findall(line)
        if fixed:
            ticket_id = fixed[0]
            if ticket_id in contributions[release]:
                continue
            backport = bool(backport_re.match(line))
            try:
                contribution = Contribution.from_trac(ticket_id, backport)
            except Exception as exc:
                continue
            contributions[release][ticket_id] = contribution
    releases.append(
        Release(
            release=release,
            contributions=sum(counts.values()),
            contributors=len(counts),
            new_contributors=new_contributors,
            over_2_contributions=sum(1 for count in counts.values() if count >= 2),
            over_10_contributions=sum(1 for count in counts.values() if count >= 10),
            features=contributions.count_type(release, ContributionType.FEATURE),
            bugfixes=contributions.count_type(release, ContributionType.BUGFIX),
            optimizations=contributions.count_type(
                release, ContributionType.OPTIMIZATION
            ),
        )
    )


contributions.cache()
writer = csv.DictWriter(sys.stdout, fieldnames=Release._fields)
writer.writeheader()
for release in releases:
    writer.writerow(release._asdict())
