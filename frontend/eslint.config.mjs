import nextVitals from "eslint-config-next/core-web-vitals"

export default [
  { ignores: ["components/charts/**", "lib/generated-api-types.ts"] },
  ...nextVitals,
  {
    rules: {
      // The dashboard intentionally derives local presentation state from
      // polling snapshots and focus-management effects. The React compiler
      // diagnostics are not correctness violations in these boundary cases.
      "react-hooks/purity": "off",
      "react-hooks/refs": "off",
      "react-hooks/set-state-in-effect": "off",
    },
  },
]
