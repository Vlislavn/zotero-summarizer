if (typeof args.task !== "string" || !args.task.trim()) {
  throw new Error("task must contain the complete current read-only smoke assignment");
}
const first = await runs.run("sequence", {
  agent: "scout",
  context: "fresh",
  task: [args.task, "Return exactly NATIVE_SEQUENCE. No edits, shell commands or delegation."].join("\n\n")
});
if (!first.ok) {
  return { transportComplete: false, stoppedAt: "sequence", first };
}
const parallel = await runs.all([
  {
    key: "left",
    agent: "scout",
    context: "fresh",
    task: [args.task, "Return exactly NATIVE_LEFT. No edits, shell commands or delegation."].join("\n\n")
  },
  {
    key: "right",
    agent: "scout",
    context: "fresh",
    task: [args.task, "Return exactly NATIVE_RIGHT. No edits, shell commands or delegation."].join("\n\n")
  }
]);
return { transportComplete: parallel.every(result => result.ok), first, parallel };
