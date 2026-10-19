import { Link } from "wouter";

import { useGearAlerts } from "../api/queries";

export function GearAlertBanner() {
  const alerts = useGearAlerts();
  if (!alerts.data?.length) return null;
  const first = alerts.data[0];
  const isTire = first.kind === "tire";
  return (
    <div className="gear-alert" role="alert">
      <strong>
        {first.brand} {first.model}
      </strong>
      {isTire && first.bike_name && ` (${first.position} tire of ${first.bike_name})`} has reached{" "}
      {Math.round(first.distance_km)} km.{" "}
      <Link href="/gear">{isTire ? "Review your tires" : "Review your shoes"}</Link>
      {alerts.data.length > 1 ? ` (${alerts.data.length} items need attention)` : ""}.
    </div>
  );
}
