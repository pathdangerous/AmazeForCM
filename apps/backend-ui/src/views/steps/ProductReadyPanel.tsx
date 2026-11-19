import { Button, Text } from "@mantine/core";

const CONSUMER_APP = "http://127.0.0.1:5174";

export default function ProductReadyPanel({ feedCode }: { feedCode: string }) {
  return (
    <div>
      <Text>The product is ready for consumers.</Text>
      <div style={{ display: "flex", gap: 12, marginTop: 12, flexWrap: "wrap" }}>
        <Button component="a" href={`${CONSUMER_APP}/#/product/${feedCode}`}>Open the product in the consumer app</Button>
        <Button component="a" variant="default" href="#/onboard">Onboard another file</Button>
      </div>
    </div>
  );
}
