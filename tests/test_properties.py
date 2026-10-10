"""Property-based tests (Hypothesis): rules that must hold for every input, not only for hand-picked examples.
They cover the code between the inverter's raw registers and what OpenAmpere shows or writes (#203)."""

from hypothesis import given
from hypothesis import strategies as st

from openampere.control import SOC_FIELDS, SOC_MIN, check_limits, write_order
from openampere.drivers.base import MAX_PLAUSIBLE_W, PvInput, Snapshot
from openampere.drivers.regs import Kind, Reg, decode, encode

RANGES = {Kind.U16: (0, 0xFFFF), Kind.I16: (-0x8000, 0x7FFF),
          Kind.U32: (0, 0xFFFFFFFF), Kind.I32: (-0x80000000, 0x7FFFFFFF)}
words = st.integers(0, 0xFFFF)


@st.composite
def reg_and_value(draw):
    kind = draw(st.sampled_from(list(Kind)))
    lo, hi = RANGES[kind]
    return Reg(0, kind), draw(st.integers(lo, hi))


@given(reg_and_value())
def test_encode_then_decode_returns_the_value(case):
    reg, value = case
    raw = encode(reg, value)
    assert len(raw) == reg.count and all(0 <= w <= 0xFFFF for w in raw)
    assert decode(reg, raw) == value


@given(st.sampled_from(list(Kind)), st.lists(words, min_size=2, max_size=2))
def test_decode_stays_in_the_range_of_the_kind(kind, raw):
    lo, hi = RANGES[kind]
    assert lo <= decode(Reg(0, kind), raw) <= hi


readings = st.one_of(st.none(), st.floats(allow_nan=False, allow_infinity=False, width=32),
                     st.integers(-0x80000000, 0xFFFFFFFF))


@given(pv=readings, house=readings, grid=readings, battery=readings, soc=readings, soh=readings,
       battery_temperature=readings, temperatures=st.dictionaries(st.sampled_from(["inverter", "ambient"]), readings),
       pv_inputs=st.lists(readings, max_size=4))
def test_sanitize_keeps_only_plausible_values(pv, house, grid, battery, soc, soh, battery_temperature, temperatures,
                                              pv_inputs):
    snap = Snapshot(timestamp=0, pv_power=pv, house_power=house, grid_power=grid, battery_power=battery,
                    battery_soc=soc, battery_soh=soh, battery_temperature=battery_temperature,
                    temperatures={k: v for k, v in temperatures.items() if v is not None},
                    pv_inputs=[PvInput(power=p) for p in pv_inputs]).sanitize()

    def within(value, lo, hi):
        return value is None or lo <= value <= hi

    assert within(snap.pv_power, 0, MAX_PLAUSIBLE_W) and within(snap.house_power, 0, MAX_PLAUSIBLE_W)
    assert within(snap.grid_power, -MAX_PLAUSIBLE_W, MAX_PLAUSIBLE_W)
    assert within(snap.battery_power, -MAX_PLAUSIBLE_W, MAX_PLAUSIBLE_W)
    assert within(snap.battery_soc, 0, 100) and within(snap.battery_soh, 0, 100)
    assert within(snap.battery_temperature, -40, 90)
    assert all(-40 <= v <= 120 for v in snap.temperatures.values())
    assert all(within(p.power, 0, MAX_PLAUSIBLE_W) for p in snap.pv_inputs)
    # a plausible value is never dropped
    if soc is not None and 0 <= soc <= 100:
        assert snap.battery_soc == soc


def valid(values: dict) -> bool:
    try:
        check_limits(values)
    except ValueError:
        return False
    return True


@st.composite
def soc_limits(draw):
    """A valid setting, built directly (0 <= min_soc <= min_soc_on_grid < max_soc <= 100) instead of filtered."""
    reserve = draw(st.integers(SOC_MIN["min_soc_on_grid"], 99))
    limits = {"min_soc": draw(st.integers(SOC_MIN["min_soc"], reserve)), "min_soc_on_grid": reserve,
              "max_soc": draw(st.integers(max(reserve + 1, SOC_MIN["max_soc"]), 100))}
    assert valid(limits)
    return limits


@given(current=soc_limits(), target=soc_limits(), keys=st.sets(st.sampled_from(SOC_FIELDS), min_size=1))
def test_every_intermediate_state_of_a_limit_change_is_valid(current, target, keys):
    """The inverter rejects (or misbehaves with) an invalid combination of limits, and the writes go out one
    register at a time. So from one valid setting to another, every state in between must be valid as well."""
    diff = {key: target[key] for key in keys}
    if not valid({**current, **diff}):
        return  # a change to an invalid target is rejected before writing
    order = write_order(current, diff)
    assert sorted(order) == sorted(diff)
    state = dict(current)
    for key in order:
        state[key] = diff[key]
        assert valid(state), f"invalid intermediate state {state} on the way from {current} to {diff}"
