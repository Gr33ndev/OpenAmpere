// SPDX-FileCopyrightText: Copyright the OpenAmpere contributors
// SPDX-License-Identifier: MIT
import { useEffect, useRef, useState } from "react";
import type { AuthStatus, Status } from "./api";
import { deleteJson, getJson, postFile } from "./api";
import { updatedLabel } from "./format";
import { t, tx } from "./i18n";
import { Button, Dialog, Notice, toast } from "./ui";

/** Picks a backup file, asks before replacing the data, uploads it and reloads the app once OpenAmpere has started
 *  again with the restored database (#165). */
export function RestoreButton({ variant = "secondary" }: { variant?: "primary" | "secondary" }) {
  const input = useRef<HTMLInputElement>(null);
  const [file, setFile] = useState<File | null>(null);
  const [phase, setPhase] = useState<"upload" | "restart" | "manual" | null>(null);
  const before = useRef<number | null>(null);

  const restore = async (chosen: File) => {
    setFile(null);
    setPhase("upload");
    try {
      before.current = (await getJson<Status>("/api/status")).database.restored?.ts ?? null;
      const result = await postFile<{ restarting: boolean }>("/api/backup/restore", chosen, "application/vnd.sqlite3");
      setPhase(result.restarting ? "restart" : "manual");
    } catch (e) {
      setPhase(null);
      toast((e as Error).message, "error");
    }
  };

  useEffect(() => {
    if (phase !== "restart") return;
    // the restored database answers with a new restore time; until then the old server or none at all
    const timer = window.setInterval(async () => {
      try {
        const status = await getJson<Status>("/api/status");
        if ((status.database.restored?.ts ?? null) !== before.current) window.location.reload();
      } catch {
        /* still starting */
      }
    }, 2000);
    return () => window.clearInterval(timer);
  }, [phase]);

  return (
    <>
      <input ref={input} type="file" hidden onChange={(e) => {
        const chosen = e.target.files?.[0];
        e.target.value = "";
        if (chosen) setFile(chosen);
      }} />
      <Button variant={variant} busy={phase === "upload"} onClick={() => input.current?.click()}>
        {t("settings.restoreCard.chooseBackup")}
      </Button>
      {phase === "manual" && <Notice kind="warn">{t("settings.restoreCard.restartManually")}</Notice>}
      {file && (
        <Dialog title={t("settings.restoreCard.confirmTitle")} confirm={t("settings.restoreCard.restore")} danger
          onConfirm={() => void restore(file)} onCancel={() => setFile(null)}>
          <p>{t("settings.restoreCard.confirmReplace", { name: file.name })}</p>
          <p className="hint">{t("settings.restoreCard.confirmKept")}</p>
        </Dialog>
      )}
      {(phase === "upload" || phase === "restart") && (
        <div className="overlay">
          <div className="dialog" role="dialog" aria-modal="true" aria-label={t("settings.restoreCard.title")}>
            <h2>{t("settings.restoreCard.title")}</h2>
            <p>{phase === "upload" ? t("settings.restoreCard.checking") : t("settings.restoreCard.restarting")}</p>
            <p className="hint">{t("settings.restoreCard.dontClose")}</p>
          </div>
        </div>
      )}
    </>
  );
}

/** The last restore: when, and where the database it replaced is kept. */
export function LastRestore({ restored }: { restored: Status["database"]["restored"] }) {
  if (!restored) return null;
  return (
    <p className="hint">
      {tx("settings.restoreCard.lastRestore", { date: updatedLabel(restored.ts),
        file: <code className="code-inline wrap">{restored.kept}</code> })}
    </p>
  );
}

/** Hides a notice on the server, for every device. */
async function dismiss(path: string, hide: () => void) {
  try {
    await deleteJson(path);
    hide();
  } catch (e) {
    toast((e as Error).message, "error");
  }
}

/** What happened to the database at the start, on every screen until the owner hides it: it could not be read and
 *  OpenAmpere began with an empty one (#165), or an older version continues with the copy from before an update
 *  (#166). */
export function DatabaseNotice({ status, auth }: { status: Status; auth: AuthStatus | null }) {
  const { damaged, rollback } = status.database ?? {};
  const [hidden, setHidden] = useState<string[]>([]);
  const hide = (kind: string) => () => setHidden((list) => [...list, kind]);
  const showDamaged = !!damaged && !hidden.includes("damaged");
  const showRollback = !!rollback && !hidden.includes("rollback");
  if (!showDamaged && !showRollback) return null;
  return (
    <div className="page top-notice">
      {damaged && showDamaged && (
        <Notice kind="error">
          <p><strong>{t("shell.databaseNotice.title")}</strong></p>
          <p>{t("shell.databaseNotice.whatHappened", { date: updatedLabel(damaged.ts) })}</p>
          <p>{tx("shell.databaseNotice.fileKept", { file: <code className="code-inline wrap">{damaged.file}</code> })}</p>
          {auth && !auth.configured
            ? <p><strong>{t("shell.databaseNotice.setPasswordNow")}</strong></p>
            : <p>{t("shell.databaseNotice.restoreHint")}</p>}
          {auth?.authenticated && (
            <div className="button-row">
              <RestoreButton variant="primary" />
              <Button variant="secondary" onClick={() => void dismiss("/api/database/damaged", hide("damaged"))}>
                {t("shell.databaseNotice.hide")}
              </Button>
            </div>
          )}
        </Notice>
      )}
      {rollback && showRollback && (
        <Notice kind="warn">
          <p><strong>{t("shell.databaseNotice.rollbackTitle")}</strong></p>
          <p>{t("shell.databaseNotice.rollbackWhat", { date: updatedLabel(rollback.ts) })}</p>
          <p>{tx("shell.databaseNotice.rollbackFileKept", { file: <code className="code-inline wrap">{rollback.kept}</code> })}</p>
          {auth?.authenticated && (
            <div className="button-row">
              <Button variant="secondary" onClick={() => void dismiss("/api/database/rollback", hide("rollback"))}>
                {t("shell.databaseNotice.hide")}
              </Button>
            </div>
          )}
        </Notice>
      )}
    </div>
  );
}
