import BellKAT.QuantumEventParser
import BellKAT.QuantumPrelude
import System.Environment (getArgs, withArgs)

p :: QBKATPolicy
p =
        while
            (   "A" /~? "C"
            &&* "B" /~? "D"
            )
        (
            (   -- generations in parallel
                ite ("A" /~? "X" &&* "A" /~? "Y" &&* "A" /~? "C") (ucreate ("A", "X")) mempty
                    <||>
                ite ("B" /~? "X" &&* "B" /~? "Y" &&* "B" /~? "D") (ucreate ("B", "X")) mempty
                    <||>
                ite ("X" /~? "Y" &&* "A" /~? "Y" &&* "B" /~? "Y") (ucreate ("X", "Y")) mempty
                    <||>
                ite ("C" /~? "Y" &&* "A" /~? "C") (ucreate ("C", "Y")) mempty
                    <||>
                ite ("D" /~? "Y" &&* "B" /~? "D") (ucreate ("D", "Y")) mempty
            )
            <> -- followed by..
            (   -- nondeterministically choose the left branch to connect to Y
                ite ("A" /~? "Y") (swap "X" ("A", "Y")) mempty
                <||>
                ite ("B" /~? "Y") (swap "X" ("B", "Y")) mempty
            )
            <> -- followed by..
            (   -- nondeterministically choose the right endpoint
                swap "Y" ("A", "C")
                <||>
                swap "Y" ("B", "D")
            )
        )


networkCapacity :: NetworkCapacity QBKATTag
networkCapacity =
    [ "A" ~ "X"
    , "B" ~ "X"
    , "X" ~ "Y"
    , "C" ~ "Y"
    , "D" ~ "Y"
    , "A" ~ "Y"
    , "B" ~ "Y"
    , "A" ~ "C"
    , "B" ~ "D"
    ]

nb :: NetworkBounds QBKATTag
nb = def
    { nbCapacity = Just networkCapacity
    , nbOperationTiming = InstantaneousOps
    }

actionConfig :: ProbabilisticActionConfiguration
actionConfig =
    PAC
        { pacTransmitProbability = []
        , pacCreateProbability = []
        , pacCreateWerner = []
        , pacUCreateProbability =
            [ (("A", "X"), 1/2)
            , (("B", "X"), 1/3)
            , (("X", "Y"), 1/3)
            , (("C", "Y"), 1/3)
            , (("D", "Y"), 1/3)
            ]
        , pacSwapProbability =
            [ ("X", 1/2),
              ("Y", 1/2)
             ]
        , pacUCreateWerner =
            [ (("A", "X"), 90/100)
            , (("B", "X"), 95/100)
            , (("X", "Y"), 95/100)
            , (("C", "Y"), 95/100)
            , (("D", "Y"), 95/100)
            ]
        , pacCoherenceTime =
            [ ("A", 100)
            , ("B", 100)
            , ("C", 100)
            , ("D", 100)
            , ("X", 1000)
            , ("Y", 1000)
            ]
        , pacDistances =
            [ (("A", "X"), 1)
            , (("B", "X"), 1)
            , (("X", "Y"), 1)
            , (("C", "Y"), 1)
            , (("D", "Y"), 1)
            , (("A", "Y"), 2)
            , (("B", "Y"), 2)
            , (("A", "C"), 3)
            , (("B", "D"), 3)
            ]
        }

main :: IO ()
main = do
    args <- getArgs
    exampleArgs <- either fail pure (stripEventArgs "A~C" args)
    ev <- either fail pure (parseQBKATEventExpr (eaEventExpr exampleArgs))
    withArgs (eaQbkatArgs exampleArgs) $
        qbkatMainD actionConfig nb ev p mempty
