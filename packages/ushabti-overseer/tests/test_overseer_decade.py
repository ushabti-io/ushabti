"""Decade assignment."""

import pytest

from ushabti_overseer.decade import DEFAULT_DECADE_SIZE, Decade, Decades


def test_the_default_decade_holds_ten() -> None:
    assert DEFAULT_DECADE_SIZE == 10
    assert Decades().size == 10


def test_the_eleventh_ushabti_starts_a_new_decade() -> None:
    decades = Decades()
    numbers = [decades.assign(f"ushabti-{n}") for n in range(1, 12)]

    assert numbers[:10] == [1] * 10, "the first ten share a decade"
    assert numbers[10] == 2, "the eleventh starts a new one"
    first, second = decades.all()
    assert first == Decade(number=1, members=tuple(f"ushabti-{n}" for n in range(1, 11)))
    assert second == Decade(number=2, members=("ushabti-11",))


def test_decades_fill_in_order() -> None:
    decades = Decades()
    numbers = [decades.assign(f"ushabti-{n}") for n in range(1, 26)]
    assert numbers == [1] * 10 + [2] * 10 + [3] * 5
    assert [len(decade.members) for decade in decades.all()] == [10, 10, 5]


def test_an_ushabti_keeps_its_decade() -> None:
    decades = Decades(size=2)
    assert decades.assign("a") == 1
    assert decades.assign("b") == 1
    assert decades.assign("c") == 2
    assert decades.assign("a") == 1, "asking again does not move it"
    assert decades.of("c") == 2
    assert decades.of("stranger") is None
    assert [len(decade.members) for decade in decades.all()] == [2, 1]


@pytest.mark.parametrize("size", [1, 3, 36])
def test_the_size_is_configurable(size: int) -> None:
    decades = Decades(size=size)
    numbers = [decades.assign(f"ushabti-{n}") for n in range(size + 1)]
    assert numbers == [1] * size + [2]


@pytest.mark.parametrize("size", [0, -1])
def test_a_decade_must_hold_someone(size: int) -> None:
    with pytest.raises(ValueError, match="at least one ushabti"):
        Decades(size=size)


def test_no_ushabti_means_no_decades() -> None:
    assert Decades().all() == ()
