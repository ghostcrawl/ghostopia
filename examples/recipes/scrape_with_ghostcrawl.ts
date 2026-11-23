// GhostCrawl SDK recipe: scrape one page and print its markdown.
// This is a GhostCrawl SDK example, not ghostopia application source.
// Install: npm install @ghostcrawl/sdk@2.3.9
// Run: GHOSTCRAWL_API_KEY=your-api-key npx tsx scrape_with_ghostcrawl.ts

import { createGhostCrawlClient } from "@ghostcrawl/sdk";

async function main(): Promise<void> {
  const client = createGhostCrawlClient({
    token: process.env.GHOSTCRAWL_API_KEY!,
  });
  const result = await client.scrape({ url: "https://example.com", format: "markdown" });
  console.log(result.status);
  console.log(String(result.markdown).slice(0, 500));
}

void main();
