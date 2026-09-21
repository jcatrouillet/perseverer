import { Link } from "wouter";

import { useGearAlerts } from "../api/queries";

export function GearAlertBanner() {
  const alerts = useGearAlerts();
  if (!alerts.data?.length) return null;
  const first = alerts.data[0];
  return (
    <div className="gear-alert" role="alert">
      <strong>
        {first.brand} {first.model}
      </strong>{" "}
      has reached {Math.round(first.distance_km)} km. <Link href="/gear">Review your shoes</Link>
      {alerts.data.length > 1 ? ` (${alerts.data.length} pairs need attention)` : ""}.
    </div>
  );
}
