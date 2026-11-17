/** The three jobs OpenKG does best, each with a question that works well as written. */
export const EXAMPLES: { id: string; label: string; who: string; gets: string; prompt: string }[] = [
  {
    id: "account", label: "Account map", who: "Sales and partnerships",
    gets: "Leadership, where they worked before, investors and recent launches for one company",
    prompt: "Account map of Stripe: executives, their previous companies, recent product launches and main investors",
  },
  {
    id: "market", label: "Market map", who: "Strategy, consulting and product",
    gets: "The players in a category, where they are based, who funds them and who supplies whom",
    prompt: "Map the lab-grown diamond market: producers, where they are based, retailers they supply and their investors",
  },
  {
    id: "deals", label: "Deal tracker", who: "Investors and corporate development",
    gets: "Who bought or funded whom, for how much and when, ready to keep watching",
    prompt: "AI company acquisitions announced in 2026: the acquirer, the target, the price and the date",
  },
];

/** Opens the example as a new graph and stops at the spending confirmation. */
export const exampleHref = (prompt: string) => `/new?quick=1&prompt=${encodeURIComponent(prompt)}`;
