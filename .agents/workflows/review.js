if (typeof args.task !== "string" || !args.task.trim()) {
  throw new Error("task must contain the complete current read-only assignment or required brief path");
}
const scan = await runs.run("scout", {
  agent: "scout",
  context: "fresh",
  task: args.task
});
if (!scan.ok) {
  return { transportComplete: false, stoppedAt: "scout", scout: scan };
}
const reviews = await runs.all([
  {
    key: "correctness",
    agent: "reviewer",
    context: "fresh",
    task: [args.task, "Review correctness and requirement coverage independently. No edits or shell commands.", "Scout evidence:", scan.output].join("\n\n")
  },
  {
    key: "evidence",
    agent: "reviewer",
    context: "fresh",
    task: [args.task, "Independently review evidence strength, safety boundaries and simplification. No edits or shell commands.", "Scout evidence:", scan.output].join("\n\n")
  }
]);
return {
  transportComplete: reviews.every(result => result.ok),
  scout: scan,
  reviews
};
