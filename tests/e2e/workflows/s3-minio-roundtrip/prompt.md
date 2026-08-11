Use only the installed `datus s3 --profile e2e` plugin commands to upload
`fixtures/events.json` to `s3://warehouse/{{RUN_ID}}/input/events.json`.

After the upload, inspect the object with the plugin and write
`results/upload.json`. The receipt must contain the S3 URI, reported byte size,
and the plugin command sequence used. Do not use aws, mc, curl, boto3, or another
S3 client.
