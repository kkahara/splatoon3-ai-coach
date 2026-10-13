import { useEffect, useState } from "react";
import {
  loadAdminPrice,
  loadAdminWorkerCapacity,
  saveAdminPrice,
  saveAdminUploadLimit,
  saveAdminWorkerCapacity,
} from "../api.js";
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
  const [uploads, setUploads] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [limitError, setLimitError] = useState("");
  const [limitSaved, setLimitSaved] = useState("");
  const [limitSaving, setLimitSaving] = useState(false);

  useEffect(() => {
    let stopped = false;
    loadAdminPrice()
      .then((next) => {
        if (stopped) {
          return;
        }
        setPrice(next);
        setDollars(next.price_cents == null ? "" : (next.price_cents / 100).toFixed(2));
        if (Number.isInteger(next.account_uploads_per_hour)) {
          setUploads(String(next.account_uploads_per_hour));
        }
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

  async function onSaveLimit(event) {
    event.preventDefault();
    setLimitError("");
    setLimitSaved("");
    const perHour = Number(uploads);
    if (!Number.isInteger(perHour) || perHour < 1 || perHour > 100) {
      setLimitError("Enter a whole number from 1 to 100.");
      return;
    }
    setLimitSaving(true);
    try {
      const next = await saveAdminUploadLimit(perHour);
      if (!Number.isInteger(next.account_uploads_per_hour)) {
        setLimitError("The upload limit was not saved.");
        return;
      }
      setPrice((current) =>
        current
          ? { ...current, account_uploads_per_hour: next.account_uploads_per_hour }
          : current,
      );
      setUploads(String(next.account_uploads_per_hour));
      setLimitSaved("Saved.");
    } catch (exc) {
      setLimitError(exc.message);
    } finally {
      setLimitSaving(false);
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
          <form onSubmit={onSaveLimit}>
            <label>
              Logged-in uploads per hour
              <input
                value={uploads}
                type="number"
                min={1}
                max={100}
                step={1}
                inputMode="numeric"
                onChange={(event) => setUploads(event.target.value)}
              />
            </label>
            <p className="note">
              Accepting the submission uses one hourly slot, including when the proxy then rejects the video.
            </p>
            <AdminError message={limitError} />
            <button type="submit" disabled={limitSaving}>
              {limitSaving ? "Saving…" : "Save upload limit"}
            </button>
          </form>
          {limitSaved ? <p className="note">{limitSaved}</p> : null}
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
      <WorkerCapacitySection />
    </section>
  );
}

function WorkerCapacitySection() {
  const [current, setCurrent] = useState(null);
  const [value, setValue] = useState("");
  const [error, setError] = useState("");
  const [saved, setSaved] = useState("");
  const [saving, setSaving] = useState(false);

  useEffect(() => {
    let stopped = false;
    loadAdminWorkerCapacity()
      .then((next) => {
        if (!stopped) {
          setCurrent(next);
          setValue(String(next.max_concurrent_jobs));
        }
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

  async function onSave(event) {
    event.preventDefault();
    setError("");
    setSaved("");
    const jobs = Number(value);
    if (!Number.isInteger(jobs) || jobs < current.min || jobs > current.max) {
      setError(`Enter a whole number from ${current.min} to ${current.max}.`);
      return;
    }
    setSaving(true);
    try {
      const next = await saveAdminWorkerCapacity(jobs);
      setCurrent(next);
      setValue(String(next.max_concurrent_jobs));
      setSaved("Saved.");
    } catch (exc) {
      setError(exc.message);
    } finally {
      setSaving(false);
    }
  }

  return (
    <>
      <h2>Worker capacity</h2>
      {!current && !error ? <AdminLoading /> : null}
      {current ? (
        <form onSubmit={onSave}>
          <label>
            Videos this worker analyzes at once
            <input
              value={value}
              type="number"
              min={current.min}
              max={current.max}
              step={1}
              inputMode="numeric"
              onChange={(event) => setValue(event.target.value)}
            />
          </label>
          <p className="note">
            Worker {current.worker_key}, {current.active_jobs} running now. This is the capacity of this
            analysis worker, not a limit for the whole site; another worker would have its own value.
            Lowering it lets running analyses finish and stops new ones until fewer than the new value are running.
          </p>
          <AdminError message={error} />
          <button type="submit" disabled={saving}>
            {saving ? "Saving…" : "Save worker capacity"}
          </button>
        </form>
      ) : (
        <AdminError message={error} />
      )}
      {saved ? <p className="note">{saved}</p> : null}
    </>
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
