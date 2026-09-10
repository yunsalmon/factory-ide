`inventory-projection-issue11.js` is the unmodified actual WIP adapter from
factory-ide issue #11, commit `b0d559b696d0d9123761d1d95dacbd5e011c7808`, path
`web/inventory-projection.js`. It is fixed regression input, not production code.
It exercises the real inferred-location adapter boundary, including its lack of
an explicit `factory-placement-v1` contract. Keep the fixture unchanged.
