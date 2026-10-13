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
  const before = useRef<string | null>(null);

  const restore = async (chosen: File) => {
    setFile(null);
    setPhase("upload");
    try {
      before.current = JSON.stringify((await getJson<Status>("/api/status")).database.restored ?? null);
      const result = await postFile<{ restarting: boolean }>("/api/backup/restore", chosen, "application/vnd.sqlite3");
      setPhase(result.restarting ? "restart" : "manual");
    } catch (e) {
      setPhase(null);
      toast((e as Error).message, "error");
    }
  };

  useEffect(() => {
    if (phase !== "restart") return;
    // the restored database answers with a new restore time (or, without login, now with one at all); until then the
    // old server or none at all
    const timer = window.setInterval(async () => {
      try {
        const status = await getJson<Status>("/api/status");
        if (JSON.stringify(status.database.restored ?? null) !== before.current) window.location.reload();
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
  if (!restored || restored === true) return null; // without login only whether there was one (#353)
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
 *  OpenAmpere began with an empty one (#165), an older version continues with the copy from before an update
 *  (#166), or Home Assistant restored a backup with the password and app accesses of then (#350). Without login the
 *  server says only that it happened, not when or which file (#353). */
export function DatabaseNotice({ status, auth }: { status: Status; auth: AuthStatus | null }) {
  const { damaged, rollback, ha_backup: haBackup } = status.database ?? {};
  const [hidden, setHidden] = useState<string[]>([]);
  const hide = (kind: string) => () => setHidden((list) => [...list, kind]);
  const showDamaged = !!damaged && !hidden.includes("damaged");
  const showRollback = !!rollback && !hidden.includes("rollback");
  const showHaBackup = !!haBackup && !hidden.includes("ha_backup");
  if (!showDamaged && !showRollback && !showHaBackup) return null;
  return (
    <div className="page top-notice">
      {damaged && showDamaged && (
        <Notice kind="error">
          <p><strong>{t("shell.databaseNotice.title")}</strong></p>
          {typeof damaged === "object" ? (
            <>
              <p>{t("shell.databaseNotice.whatHappened", { date: updatedLabel(damaged.ts) })}</p>
              <p>{tx("shell.databaseNotice.fileKept", { file: <code className="code-inline wrap">{damaged.file}</code> })}</p>
            </>
          ) : <p>{t("shell.databaseNotice.whatHappenedNoDate")}</p>}
          {auth?.locked ? null : auth && !auth.configured
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
          {typeof rollback === "object" ? (
            <>
              <p>{t("shell.databaseNotice.rollbackWhat", { date: updatedLabel(rollback.ts) })}</p>
              <p>{tx("shell.databaseNotice.rollbackFileKept", { file: <code className="code-inline wrap">{rollback.kept}</code> })}</p>
            </>
          ) : <p>{t("shell.databaseNotice.rollbackWhatNoDate")}</p>}
          {auth?.authenticated && (
            <div className="button-row">
              <Button variant="secondary" onClick={() => void dismiss("/api/database/rollback", hide("rollback"))}>
                {t("shell.databaseNotice.hide")}
              </Button>
            </div>
          )}
        </Notice>
      )}
      {haBackup && showHaBackup && (
        <Notice kind="warn">
          <p><strong>{t("shell.databaseNotice.haBackupTitle")}</strong></p>
          <p>{typeof haBackup === "object"
            ? t("shell.databaseNotice.haBackupWhat", { date: updatedLabel(haBackup.ts) })
            : t("shell.databaseNotice.haBackupWhatNoDate")}</p>
          <p>{t("shell.databaseNotice.haBackupCheck")}</p>
          {auth?.authenticated && (
            <div className="button-row">
              <Button variant="secondary" onClick={() => void dismiss("/api/database/ha-backup", hide("ha_backup"))}>
                {t("shell.databaseNotice.hide")}
              </Button>
            </div>
          )}
        </Notice>
      )}
    </div>
  );
}
