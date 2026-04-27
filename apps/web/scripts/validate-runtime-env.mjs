const secret = process.env.AQ_SESSION_SECRET;
if (!secret || secret.length < 32) {
  console.error("AQ_SESSION_SECRET must be set and at least 32 characters.");
  process.exit(1);
}

const secure = process.env.AQ_COOKIE_SECURE;
if (secure !== undefined && secure !== "true" && secure !== "false") {
  console.error("AQ_COOKIE_SECURE must be either true or false when set.");
  process.exit(1);
}
