# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from poresira.core.tests.factories import UserFactory
from poresira.samples.lineage import ancestors, descendants, family_tree
from poresira.samples.models import SampleKind
from poresira.samples.tests.factories import DerivativeFactory, SampleFactory

pytestmark = pytest.mark.django_db


@pytest.fixture
def family():
    """leaf -> dna -> (rca, rca2); leaf -> rna; a second, unrelated leaf."""
    leaf = SampleFactory(label="BFA1")
    dna = DerivativeFactory(parent=leaf, kind=SampleKind.DNA_EXTRACT)
    rca = DerivativeFactory(parent=dna, kind=SampleKind.AMPLIFICATION_PRODUCT)
    rca_repeat = DerivativeFactory(parent=dna, kind=SampleKind.AMPLIFICATION_PRODUCT)
    rna = DerivativeFactory(parent=leaf, kind=SampleKind.RNA_EXTRACT)
    return {
        "leaf": leaf,
        "dna": dna,
        "rca": rca,
        "rca_repeat": rca_repeat,
        "rna": rna,
        "other": SampleFactory(),
    }


def test_ancestors_run_from_the_root_down_to_the_parent(family):
    assert ancestors(family["rca"]) == [family["leaf"], family["dna"]]
    assert ancestors(family["dna"]) == [family["leaf"]]


def test_a_root_sample_has_no_ancestors(family):
    assert ancestors(family["leaf"]) == []


def test_descendants_come_depth_first_with_their_depth(family):
    nodes = descendants(family["leaf"])
    by_sample = {node.sample: node.depth for node in nodes}

    assert by_sample == {
        family["dna"]: 1,
        family["rca"]: 2,
        family["rca_repeat"]: 2,
        family["rna"]: 1,
    }
    assert family["leaf"] not in by_sample
    dna_position = [node.sample for node in nodes].index(family["dna"])
    assert {node.sample for node in nodes[dna_position + 1 : dna_position + 3]} == {
        family["rca"],
        family["rca_repeat"],
    }


def test_descendants_of_a_leaf_node_are_empty(family):
    assert descendants(family["rca"]) == []


def test_other_families_are_not_mixed_in(family):
    assert family["other"] not in {node.sample for node in family_tree(family["rca"])}


def test_family_tree_starts_at_the_root_whichever_sample_it_is_asked_for(family):
    from_leaf = family_tree(family["leaf"])
    from_rca = family_tree(family["rca"])

    assert from_leaf == from_rca
    assert from_leaf[0].sample == family["leaf"]
    assert from_leaf[0].depth == 0
    assert len(from_leaf) == 5


def test_voided_samples_are_hidden_unless_asked_for(family):
    family["rca"].void(user=UserFactory(), reason="Failed amplification")

    assert family["rca"] not in {node.sample for node in descendants(family["leaf"])}
    assert family["rca"] in {
        node.sample for node in descendants(family["leaf"], include_voided=True)
    }
    assert family["rca"] in {node.sample for node in family_tree(family["leaf"])}


def test_a_voided_branch_hides_everything_below_it(family):
    family["rca"].void(user=UserFactory(), reason="Failed")
    family["rca_repeat"].void(user=UserFactory(), reason="Failed")
    family["dna"].void(user=UserFactory(), reason="Wrong extraction")

    assert {node.sample for node in descendants(family["leaf"])} == {family["rna"]}


def test_deep_lineages_need_a_constant_number_of_queries():
    sample = SampleFactory()
    for _generation in range(12):
        sample = DerivativeFactory(parent=sample, kind=SampleKind.DNA_EXTRACT)

    with CaptureQueriesContext(connection) as queries:
        chain = ancestors(sample)

    assert len(chain) == 12
    assert len(queries) == 2
