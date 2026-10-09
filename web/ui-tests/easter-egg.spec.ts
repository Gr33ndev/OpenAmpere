import { expect, open, test } from "./fixtures";

const ARROWS = ["ArrowUp", "ArrowUp", "ArrowDown", "ArrowDown", "ArrowLeft", "ArrowRight", "ArrowLeft", "ArrowRight"];

test("the easter egg code on the keyboard switches the energy flow to the retro look and back", async ({ page }) => {
  await open(page, "");
  const flow = page.locator(".flow");
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(flow).toHaveClass(/retro/);
  await expect(page.locator(".retro-banner")).toBeVisible();
  for (const key of [...ARROWS, "b", "a"]) await page.keyboard.press(key);
  await expect(flow).not.toHaveClass(/retro/);
});

test("the easter egg code can be tapped on the energy flow, B and A appear as buttons", async ({ page }) => {
  await open(page, "");
  const icon = (name: string) => page.locator(`.flow-node.${name} .flow-icon`);
  for (const name of ["sun", "sun", "house", "house", "battery", "grid", "battery", "grid"]) await icon(name).click();
  await page.locator(".retro-pad .b").click();
  await page.locator(".retro-pad .a").click();
  await expect(page.locator(".flow")).toHaveClass(/retro/);
  await expect(page.locator(".retro-pad")).toHaveCount(0);
});

test("a wrong key starts the code over", async ({ page }) => {
  await open(page, "");
  for (const key of ["ArrowUp", "ArrowUp", "ArrowLeft", ...ARROWS.slice(2), "b", "a"]) await page.keyboard.press(key);
  await expect(page.locator(".flow")).not.toHaveClass(/retro/);
});
