"""Formatting variants must not raise ID or address mismatches; real differences still must.

A false ID_MISMATCH can hold a good deal, the costliest error, so the comparison has to survive
accents, directional words, street-type abbreviations and ZIP+4, all of which a hidden set can use.
"""
import pytest

from prism.fraud.canon import canon_address, canon_text
from prism.read.compare import _address_agrees, _name_agrees

SAME_NAMES = [
    ("José Núñez", "Jose Nunez"),
    ("María Elena Garza", "Maria Elena Garza"),
    ("Robert J. Smith", "Bob Smith"),
    ("Smith, Robert", "Robert Smith"),
    ("JAMES OKAFOR", "James Okafor"),
]
DIFFERENT_NAMES = [
    ("James Okafor", "John Okafor"),
    ("Maria Garza", "Mario Garza"),
]
SAME_ADDRESSES = [
    ("2214 W Spring Valley Rd, Richardson, TX 75080", "2214 West Spring Valley Road, Richardson TX 75080-1234"),
    ("410 S Greenville Ave Ste 200, Richardson, TX 75081", "410 South Greenville Avenue Suite 200 Richardson TX 75081"),
    ("9020 June Lake Rd, Dallas, TX 75217", "9020 JUNE LAKE ROAD DALLAS TX 75217"),
    ("5900 Legacy Pkwy, Plano, TX 75024", "5900 Legacy Parkway Plano TX 75024"),
]
DIFFERENT_ADDRESSES = [
    ("2214 W Spring Valley Rd, Richardson, TX 75080", "2241 W Spring Valley Rd, Richardson, TX 75080"),
    ("410 S Greenville Ave, Richardson, TX 75081", "410 N Greenville Ave, Richardson, TX 75081"),
    ("9020 June Lake Rd, Dallas, TX 75217", "9020 June Lake Rd, Dallas, TX 75218"),
    ("9020 June Lake Rd, Dallas, TX 75217", "9020 June Lake Rd, Austin, TX 78701"),
]


@pytest.mark.parametrize("a,b", SAME_NAMES)
def test_name_variants_agree(a, b):
    assert _name_agrees(a, b, False)


@pytest.mark.parametrize("a,b", DIFFERENT_NAMES)
def test_different_names_disagree(a, b):
    assert not _name_agrees(a, b, False)


@pytest.mark.parametrize("a,b", SAME_ADDRESSES)
def test_address_variants_agree(a, b):
    assert _address_agrees(a, b, False)
    assert canon_address(a) == canon_address(b)


@pytest.mark.parametrize("a,b", DIFFERENT_ADDRESSES)
def test_different_addresses_disagree(a, b):
    assert not _address_agrees(a, b, False)


def test_zip_plus_four_keeps_the_zip():
    assert "75080" in canon_address("1 Main St, Dallas TX 75080-1234").split()


def test_accents_fold():
    assert canon_text("Núñez Café") == "nunez cafe"
