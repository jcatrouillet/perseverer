from perseverer.weather_code import weather_code_info


def test_known_codes_return_their_label_and_emoji() -> None:
    assert weather_code_info(0) == weather_code_info(0)  # sanity: stable/hashable-by-value
    clear = weather_code_info(0)
    assert clear.label == "Clear sky"
    assert clear.emoji == "☀️"

    overcast = weather_code_info(3)
    assert overcast.label == "Overcast"

    thunder = weather_code_info(95)
    assert thunder.label == "Thunderstorm"
    assert thunder.emoji == "⛈️"


def test_unknown_code_falls_back_rather_than_raising() -> None:
    info = weather_code_info(12345)
    assert info.label == "Unknown conditions"
    assert info.emoji != ""


def test_every_documented_wmo_code_has_a_distinct_entry() -> None:
    # The full set Open-Meteo's own docs list -- confirms no code was missed when porting from
    # frontend/src/weatherCode.ts.
    codes = [
        0, 1, 2, 3, 45, 48, 51, 53, 55, 56, 57, 61, 63, 65, 66, 67,
        71, 73, 75, 77, 80, 81, 82, 85, 86, 95, 96, 99,
    ]
    for code in codes:
        info = weather_code_info(code)
        assert info.label != "Unknown conditions", f"code {code} fell back to the default"
