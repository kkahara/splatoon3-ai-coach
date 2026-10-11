import { useEffect, useState } from "react";
import { loadAdminPrice, saveAdminPrice } from "../api.js";
import { centsFromDollars, formatUsd } from "./submitForm.js";
import { AdminError, AdminLoading } from "./AdminUi.jsx";

const EVENT_LABELS = {
  page_viewed: "Submit page viewed",
  upload_started: "Upload started",
  upload_completed: "Upload completed",
  checkout_opened: "Checkout opened",
  payment_completed: "Payment completed",
  analysis_completed: "Analysis completed",
  analysis_failed: "Analysis failed",
};

export function AdminPricePage() {
  const [price, setPrice] = useState(null);
  const [dollars, setDollars] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");

  useEffect(() => {
    let stopped = false;
    loadAdminPrice()
      .then((next) => {
        if (stopped) {
          return;
        }
        setPrice(next);
        setDollars(next.price_cents == null ? "" : (next.price_cents / 100).toFixed(2));
      })
      .catch((exc) => {
        if (!stopped) {
          setError(exc.message);
        }
      });
    return () => {
      stopped = true;
    };
  }, []);

  async function onSubmit(event) {
    event.preventDefault();
    setError("");
    setSaved("");
    const cents = centsFromDollars(dollars);
    if (cents == null) {
      setError("Enter a dollar amount with at most two decimal places.");
      return;
    }
    try {
      const next = await saveAdminPrice(cents);
      setPrice(next);
      setDollars((next.price_cents / 100).toFixed(2));
      setSaved("Saved.");
    } catch (exc) {
      setError(exc.message);
    }
  }

  return (
    <section>
      <h1>Price</h1>
      <AdminError message={error} />
      {!price && !error ? <AdminLoading /> : null}
      {price ? (
        <>
          <form onSubmit={onSubmit}>
            <label>
              Price per analysis (USD)
              <input
                value={dollars}
                inputMode="decimal"
                onChange={(event) => setDollars(event.target.value)}
              />
            </label>
            <button type="submit">Save price</button>
          </form>
          {saved ? <p className="note">{saved}</p> : null}
          <p className="note">
            Current version {price.pricing_version ?? "—"}
            {price.price_cents == null ? "" : `, ${formatUsd(price.price_cents)}`}
          </p>
          <h2>Events</h2>
          <p className="note">
            Counts only. Page views include refreshes and repeat visits, so they are not a conversion rate.
            Compare upload started, checkout opened, and payment completed.
          </p>
          <EventTable events={price.events || []} />
        </>
      ) : null}
    </section>
  );
}

function EventTable({ events }) {
  return (
    <div className="admin-table-wrap">
      <table className="admin-table">
        <thead>
          <tr>
            <th>Version</th>
            <th>Price</th>
            <th>Event</th>
            <th>Count</th>
          </tr>
        </thead>
        <tbody>
          {events.length === 0 ? (
            <tr>
              <td colSpan={4}>No events yet.</td>
            </tr>
          ) : null}
          {events.map((row) => (
            <tr key={`${row.pricing_version}-${row.price_cents}-${row.kind}`}>
              <td>{row.pricing_version ?? "—"}</td>
              <td>{row.price_cents == null ? "—" : formatUsd(row.price_cents)}</td>
              <td>{EVENT_LABELS[row.kind] || row.kind}</td>
              <td>{row.count}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}
