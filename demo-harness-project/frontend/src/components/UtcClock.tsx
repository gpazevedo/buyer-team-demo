import { useEffect, useState } from "react";

function pad(n: number): string {
  return n.toString().padStart(2, "0");
}

// Local wall-clock time, labeled with its actual UTC offset — matches what
// the AWS console (Step Functions, X-Ray, CloudWatch) shows in the browser's
// own timezone, so the clock and the AWS timestamps read the same during a
// live walkthrough.
function formatLocal(date: Date): string {
  const y = date.getFullYear();
  const mo = pad(date.getMonth() + 1);
  const d = pad(date.getDate());
  const h = pad(date.getHours());
  const mi = pad(date.getMinutes());
  const s = pad(date.getSeconds());
  const offsetHours = -date.getTimezoneOffset() / 60;
  const sign = offsetHours >= 0 ? "+" : "-";
  return `${y}-${mo}-${d} ${h}:${mi}:${s} UTC${sign}${Math.abs(offsetHours)}`;
}

export default function UtcClock() {
  const [now, setNow] = useState(() => new Date());

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);

  return <span className="font-mono text-xs text-gray-400">{formatLocal(now)}</span>;
}
