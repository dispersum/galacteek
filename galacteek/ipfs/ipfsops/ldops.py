import os.path
import orjson
import aioipfs

from galacteek import log

from galacteek.core import runningApp
from galacteek.core.asynclib import async_enterable
from galacteek.core.asynclib import asyncReadFile
from galacteek.ipfs.cidhelpers import IPFSPath
from galacteek.ipfs.cidhelpers import cidValid
from galacteek.ld.ldloader import aioipfs_document_loader

from galacteek.ld import asyncjsonld as jsonld
from galacteek.ld import ldContextsRootPath
from galacteek.ld import gLdBaseUri
from galacteek.ld.rdf import BaseGraph


class LDOpsContext(object):
    def __init__(self, operator, ldDocLoader):
        self.operator = operator
        self.ldLoader = ldDocLoader

    async def __aenter__(self):
        return self

    async def expandDocument(self, doc):
        """
        Perform a JSON-LD expansion on a JSON document
        """

        try:
            expanded = await jsonld.expand(
                await self.operator.ldInline(doc), {
                    'documentLoader': self.ldLoader
                }
            )

            if isinstance(expanded, list) and len(expanded) > 0:
                return expanded[0]
        except jsonld.SyntaxError as serr:
            self.operator.debug(f'JSON-LD syntax error: {serr}')
            raise serr
        except Exception as err:
            self.operator.debug('Error expanding document: {}'.format(
                str(err)))

    async def dagExpandAggressive(self,
                                  ipfsPath: IPFSPath,
                                  expandContext=None,
                                  expandContextFromType=True):
        """

        "Wanna know how i got these scars ?"

        Aggressive expansion
        """
        try:
            dag = await self.operator.dagGet(str(ipfsPath))
            assert dag is not None

            #
            # Before expanding, set some attributes
            # to keep track of the object from RDF
            #

            if isinstance(dag, dict):
                # Backlink
                dag['comesFromIpfs'] = str(ipfsPath)

                #
                # Remove reserved keywords
                #
                if 'previous' in dag:
                    del dag['previous']

            options = {
                'base': gLdBaseUri,
                'documentLoader': self.ldLoader
            }

            if expandContext:
                options['expandContext'] = {
                    '@context': expandContext
                }

            if expandContextFromType and not expandContext:
                ldType = dag.get('@type', None)
                if ldType:
                    options['expandContext'] = {
                        '@context': f'{gLdBaseUri}/{ldType}'
                    }

            expanded = await jsonld.expand(
                await self.operator.ldInline(dag),
                options
            )

            if not isinstance(expanded, list):
                raise Exception('Empty expand')
        except Exception as err:
            self.operator.debug(f'Error expanding : {err}')
            raise err
        except aioipfs.APIError as err:
            self.operator.debug(f'IPFS error expanding : {err.message}')
            raise err
        else:
            return expanded

    async def dagAsRdf(self, ipfsPath: IPFSPath):
        """
        IPFS DAG to RDF, via the rdflib-jsonld plugin
        """
        try:
            # Expand
            dag = await self.dagExpandAggressive(ipfsPath)
            assert dag is not None

            # Build the RDF graph from the expanded JSON-LD
            graph = BaseGraph()

            graph.parse(
                data=orjson.dumps(dag).decode(),
                format='json-ld'
            )

            if not graph:
                raise Exception('Graph is empty')

            return graph
        except Exception as err:
            log.debug(f'DAG to RDF error for {ipfsPath}: {err}')
            return None

    async def dagCompact(self, ipfsPath: IPFSPath, context=None):
        """
        Compact a DAG
        """
        try:
            # Note: we still do the schemas inlining (when
            # a @context is referenced with IPLD) to
            # support DAGs which weren't upgraded yet
            dag = await self.operator.ldInline(
                await self.operator.dagGet(str(ipfsPath))
            )
            assert dag is not None

            if not context:
                # Get the @context we'll compact with
                context = await self.operator.dagGet(
                    str(ipfsPath.child('@context'))
                )

            compacted = await jsonld.compact(
                dag, context,
                {
                    'base': gLdBaseUri,
                    'documentLoader': self.ldLoader,
                    'compactArrays': True
                }
            )
        except Exception as err:
            self.operator.debug(f'Error expanding : {err}')
            raise err
        except aioipfs.APIError as err:
            self.operator.debug(f'IPFS error expanding : {err.message}')
            raise err
        else:
            return compacted

    async def __aexit__(self, *args):
        pass


class LinkedDataOps(object):
    @ async_enterable
    async def ldOps(self):
        app = runningApp()

        if not self._ldDocLoader:
            self._ldDocLoader = await aioipfs_document_loader(
                self.client,
                app.ldSchemas
            )

        return LDOpsContext(
            self,
            self._ldDocLoader
        )

    async def ldInline(self, dagData):
        # In-line the JSON-LD contexts for JSON-LD usage
        #
        # XXX: FFS this is almost certainly wrong

        async def process(data):
            if isinstance(data, dict):
                for objKey, objValue in data.copy().items():
                    if objKey == '@context' and isinstance(objValue, dict):
                        link = objValue.get('/')
                        if isinstance(link, str) and cidValid(link):
                            try:
                                ctx = await self.catObject(link)
                                if ctx:
                                    data.update(orjson.loads(ctx.decode()))
                            except Exception as err:
                                self.debug('ldInline error: {}'.format(
                                    str(err)))

                            await self.sleep()
                    else:
                        await process(objValue)
            elif isinstance(data, list):
                for node in data:
                    await process(node)

            return data

        return await process(dagData)

    async def ldContext(self, cName: str, source=None,
                        key=None):
        specPath = os.path.join(
            ldContextsRootPath(),
            '{context}'.format(
                context=cName
            )
        )

        if not os.path.isfile(specPath):
            return None

        try:
            with open(specPath, 'r') as fd:
                data = fd.read()

            entry = await self.addString(data)
        except Exception as err:
            self.debug(str(err))
        else:
            return self.ipld(entry)

    async def ldContextJson(self, cName: str):
        specPath = os.path.join(
            ldContextsRootPath(),
            '{context}'.format(
                context=cName
            )
        )

        if not os.path.isfile(specPath):
            return None

        try:
            data = await asyncReadFile(specPath, mode='rt')
            return orjson.loads(data)
        except Exception as err:
            self.debug(str(err))
