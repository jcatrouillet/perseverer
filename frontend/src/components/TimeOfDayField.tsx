// Replaces a native <input type="time"> wherever a scheduled-workout time-of-day is entered.
// The native control's *value* is always a 24h "HH:MM" string per spec, but it *displays*
// itself in whatever format the browser/OS locale picks -- Firefox and Safari ignore the `lang`
// attribute for this, so there's no reliable way to force 24h (or a chosen 12h/AM-PM look) with
// the native element alone. This is a small, fully custom control instead: it always stores and
// emits the same 24h "HH:MM" string (drop-in replacement, same value/onChange contract), but
// renders per the athlete's own Personalize time-format setting rather than the browser's.
import { useTimeFormat } from "../formatTime";

function parseHHMM(value: string): { hour24: number; minute: number } {
  const [h, m] = value.split(":").map(Number);
  return { hour24: Number.isFinite(h) ? h! : 0, minute: Number.isFinite(m) ? m! : 0 };
}

function toHHMM(hour24: number, minute: number): string {
  return `${hour24.toString().padStart(2, "0")}:${minute.toString().padStart(2, "0")}`;
}

export function TimeOfDayField({
  value,
  onChange,
  disabled,
}: {
  /** Always a 24h "HH:MM" string, matching planned_workout.scheduled_time's own storage shape. */
  value: string;
  onChange: (value: string) => void;
  disabled?: boolean;
}) {
  const { format } = useTimeFormat();
  const is12h = format === "12h";
  const { hour24, minute } = parseHHMM(value || "00:00");
  const period: "AM" | "PM" = hour24 < 12 ? "AM" : "PM";
  const hour12 = hour24 % 12 === 0 ? 12 : hour24 % 12;
  const displayHour = is12h ? hour12 : hour24;

  function setHour(raw: number) {
    if (!Number.isFinite(raw)) return;
    if (is12h) {
      const clamped = Math.min(12, Math.max(1, Math.round(raw)));
      const strippedTo24 = clamped % 12; // 12 AM/PM -> 0
      onChange(toHHMM(period === "PM" ? strippedTo24 + 12 : strippedTo24, minute));
    } else {
      onChange(toHHMM(Math.min(23, Math.max(0, Math.round(raw))), minute));
    }
  }

  function setMinute(raw: number) {
    if (!Number.isFinite(raw)) return;
    onChange(toHHMM(hour24, Math.min(59, Math.max(0, Math.round(raw)))));
  }

  function setPeriod(nextPeriod: "AM" | "PM") {
    if (nextPeriod === period) return;
    const hourWithoutPeriod = hour24 % 12;
    onChange(toHHMM(nextPeriod === "PM" ? hourWithoutPeriod + 12 : hourWithoutPeriod, minute));
  }

  return (
    <div className="time-of-day-field">
      <input
        className="input time-of-day-field__number"
        type="number"
        inputMode="numeric"
        aria-label="Hour"
        min={is12h ? 1 : 0}
        max={is12h ? 12 : 23}
        value={displayHour}
        disabled={disabled}
        onChange={(e) => setHour(e.target.valueAsNumber)}
      />
      <span className="time-of-day-field__sep">:</span>
      <input
        className="input time-of-day-field__number"
        type="number"
        inputMode="numeric"
        aria-label="Minute"
        min={0}
        max={59}
        value={minute.toString().padStart(2, "0")}
        disabled={disabled}
        onChange={(e) => setMinute(e.target.valueAsNumber)}
      />
      {is12h && (
        <div className="time-of-day-field__period" role="group" aria-label="AM or PM">
          <button
            type="button"
            className={period === "AM" ? "is-active" : undefined}
            disabled={disabled}
            onClick={() => setPeriod("AM")}
          >
            AM
          </button>
          <button
            type="button"
            className={period === "PM" ? "is-active" : undefined}
            disabled={disabled}
            onClick={() => setPeriod("PM")}
          >
            PM
          </button>
        </div>
      )}
    </div>
  );
}
