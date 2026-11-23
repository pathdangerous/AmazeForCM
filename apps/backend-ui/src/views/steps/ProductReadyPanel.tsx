import { Button, Text } from "@mantine/core";

import { personaUrl } from "../../../../ui-shared/devPersona";

export default function ProductReadyPanel({ feedCode }: { feedCode: string }) {
  return (
    <div>
      <Text>The product is ready for consumers.</Text>
      <div style={{ display: "flex", gap: 12, marginTop: 12, flexWrap: "wrap" }}>
        <Button component="a" href={`${personaUrl({ id: "dev-requester", app: "consumer" })}#/product/${feedCode}`}>Open the product in the consumer app</Button>
        <Button component="a" variant="default" href="#/onboard">Onboard another file</Button>
      </div>
    </div>
  );
}
